import datetime
import json

import mock
import pytest

from django.core.urlresolvers import reverse
from django.conf import settings
from django.utils import timezone

from crashstats.crashstats.tests.test_views import BaseTestViews, Response
from crashstats.crashstats.models import CrontabberState
from crashstats.supersearch.models import SuperSearch
from crashstats.monitoring.views import assert_supersearch_no_errors


class TestViews(BaseTestViews):

    def test_index(self):
        url = reverse('monitoring:index')
        response = self.client.get(url)
        assert response.status_code == 200

        assert reverse('monitoring:crontabber_status') in response.content


class TestCrontabberStatusViews(BaseTestViews):

    def test_crontabber_status_ok(self):

        def mocked_get(**options):
            recently = timezone.now()
            return {
                'state': {
                    'job1': {
                        'error_count': 0,
                        'depends_on': [],
                        'last_run': recently,
                    }
                }
            }

        CrontabberState.implementation().get.side_effect = mocked_get

        url = reverse('monitoring:crontabber_status')
        response = self.client.get(url)
        assert response.status_code == 200
        assert json.loads(response.content) == {'status': 'ALLGOOD'}

    def test_crontabber_status_trouble(self):

        def mocked_get(**options):
            recently = timezone.now()
            return {
                'state': {
                    'job1': {
                        'error_count': 1,
                        'depends_on': [],
                        'last_run': recently,
                    },
                    'job2': {
                        'error_count': 0,
                        'depends_on': ['job1'],
                        'last_run': recently,
                    },
                    'job3': {
                        'error_count': 0,
                        'depends_on': ['job2'],
                        'last_run': recently,
                    },
                    'job1b': {
                        'error_count': 0,
                        'depends_on': [],
                        'last_run': recently,
                    },
                }
            }

        CrontabberState.implementation().get.side_effect = mocked_get

        url = reverse('monitoring:crontabber_status')
        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['status'] == 'Broken'
        assert data['broken'] == ['job1']
        assert data['blocked'] == ['job2', 'job3']

    def test_crontabber_status_not_run_for_a_while(self):

        some_time_ago = (
            timezone.now() - datetime.timedelta(
                minutes=settings.CRONTABBER_STALE_MINUTES
            )
        )

        def mocked_get(**options):
            return {
                'state': {
                    'job1': {
                        'error_count': 0,
                        'depends_on': [],
                        'last_run': some_time_ago,
                    },
                    'job2': {
                        'error_count': 0,
                        'depends_on': ['job1'],
                        'last_run': some_time_ago,
                    },
                }
            }

        CrontabberState.implementation().get.side_effect = mocked_get

        url = reverse('monitoring:crontabber_status')
        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['status'] == 'Stale'
        assert data['last_run'] == some_time_ago.isoformat()

    def test_crontabber_status_never_run(self):

        def mocked_get(**options):
            return {
                'state': {}
            }

        CrontabberState.implementation().get.side_effect = mocked_get

        url = reverse('monitoring:crontabber_status')
        response = self.client.get(url)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data['status'] == 'Stale'


class TestHealthcheckViews(BaseTestViews):

    def test_healthcheck_elb(self):
        url = reverse('monitoring:healthcheck')
        response = self.client.get(url, {'elb': 'true'})
        assert response.status_code == 200
        assert json.loads(response.content)['ok'] is True

        # This time, ignoring the results, make sure that running
        # this does not cause an DB queries.
        self.assertNumQueries(
            0,
            self.client.get,
            url,
            {'elb': 'true'}
        )

    @mock.patch('requests.get')
    @mock.patch('crashstats.monitoring.views.elasticsearch')
    def test_healthcheck(self, mocked_elasticsearch, rget):
        searches = []

        def mocked_supersearch_get(**params):
            searches.append(params)
            assert params['product'] == [settings.DEFAULT_PRODUCT]
            assert params['_results_number'] == 1
            assert params['_columns'] == ['uuid']
            return {
                'hits': [
                    {'uuid': '12345'},
                ],
                'facets': [],
                'total': 30002,
                'errors': [],
            }

        SuperSearch.implementation().get.side_effect = (
            mocked_supersearch_get
        )

        def mocked_requests_get(url, **params):
            return Response(True)

        rget.side_effect = mocked_requests_get

        url = reverse('monitoring:healthcheck')
        response = self.client.get(url)
        assert response.status_code == 200
        assert json.loads(response.content)['ok'] is True

        assert len(searches) == 1

    def test_assert_supersearch_errors(self):
        searches = []

        def mocked_supersearch_get(**params):
            searches.append(params)
            assert params['product'] == [settings.DEFAULT_PRODUCT]
            assert params['_results_number'] == 1
            assert params['_columns'] == ['uuid']
            return {
                'hits': [
                    {'uuid': '12345'},
                ],
                'facets': [],
                'total': 320,
                'errors': ['bad'],
            }

        SuperSearch.implementation().get.side_effect = (
            mocked_supersearch_get
        )
        with pytest.raises(AssertionError):
            assert_supersearch_no_errors()

        assert len(searches) == 1
