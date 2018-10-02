from StringIO import StringIO

import mock
import pytest

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from crashstats.base.tests.testbase import DjangoTestCase
from crashstats.authentication.management.commands import makesuperuser


class TestMakeSuperuserCommand(DjangoTestCase):
    def test_make_existing_user(self):
        bob = User.objects.create(username='bob', email='bob@mozilla.com')
        buffer = StringIO()
        call_command('makesuperuser', 'BOB@mozilla.com', stdout=buffer)
        assert 'bob@mozilla.com is now a superuser/staff' in buffer.getvalue()

        # Reload user and verify
        user = User.objects.get(pk=bob.pk)
        assert user.is_superuser
        assert user.is_staff
        assert [g.name for g in user.groups.all()] == ['Hackers']

    def test_make_already_user(self):
        bob = User.objects.create(username='bob', email='bob@mozilla.com')
        bob.is_superuser = True
        bob.is_staff = True
        bob.save()
        buffer = StringIO()
        call_command('makesuperuser', 'BOB@mozilla.com', stdout=buffer)

        # Assert what got printed
        assert 'bob@mozilla.com was already a superuser/staff' in buffer.getvalue()

        # Reload user object and verify changes
        user = User.objects.get(pk=bob.pk)
        assert user.is_superuser
        assert user.is_staff
        assert [g.name for g in user.groups.all()] == ['Hackers']

    def test_make_two_user_superuser(self):
        bob = User.objects.create(username='bob', email='bob@mozilla.com')
        bob.is_superuser = True  # already
        bob.save()
        otto = User.objects.create(username='otto', email='otto@mozilla.com')

        buffer = StringIO()
        call_command('makesuperuser', 'BOB@mozilla.com', 'oTTo@mozilla.com', stdout=buffer)
        assert 'bob@mozilla.com is now a superuser/staff' in buffer.getvalue()
        assert 'otto@mozilla.com is now a superuser/staff' in buffer.getvalue()

        # Reload user objects and verify changes
        bob = User.objects.get(pk=bob.pk)
        assert bob.is_superuser
        assert bob.is_staff
        assert [g.name for g in bob.groups.all()] == ['Hackers']

        otto = User.objects.get(pk=otto.pk)
        assert otto.is_superuser
        assert otto.is_staff
        assert [g.name for g in otto.groups.all()] == ['Hackers']

    def test_nonexisting_user(self):
        buffer = StringIO()
        email = 'neverheardof@mozilla.com'
        call_command('makesuperuser', email, stdout=buffer)
        assert '{} is now a superuser/staff'.format(email) in buffer.getvalue()

        neverheardof = User.objects.get(email=email)
        assert neverheardof.is_superuser
        assert neverheardof.is_staff
        assert [g.name for g in neverheardof.groups.all()] == ['Hackers']

    @mock.patch(
        'crashstats.authentication.management.commands.makesuperuser.'
        'get_input',
        return_value='BOB@mozilla.com '
    )
    def test_with_raw_input(self, mocked_raw_input):
        bob = User.objects.create(username='bob', email='bob@mozilla.com')
        buffer = StringIO()
        cmd = makesuperuser.Command()
        cmd.stdout = buffer
        cmd.handle(emailaddress=[])

        # reload
        bob = User.objects.get(pk=bob.pk)
        assert bob.is_superuser
        assert bob.is_staff
        assert [g.name for g in bob.groups.all()] == ['Hackers']

    @mock.patch(
        'crashstats.authentication.management.commands.makesuperuser.'
        'get_input',
        return_value='\n'
    )
    def test_with_raw_input_but_empty(self, mocked_raw_input):
        with pytest.raises(CommandError):
            buffer = StringIO()
            cmd = makesuperuser.Command()
            cmd.stdout = buffer
            cmd.handle(emailaddress=[])
