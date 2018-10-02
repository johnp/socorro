# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import mock

from socorro.cron.crontabber_app import CronTabberApp
from socorro.unittest.cron.jobs.base import IntegrationTestBase


class FakeModel(object):
    def __init__(self):
        self._get_steps = []

    def add_get_step(self, response):
        self._get_steps.append({
            'response': response
        })

    def get(self, *args, **kwargs):
        if not self._get_steps:
            raise Exception('Unexpected call to .get()')

        step = self._get_steps.pop(0)
        return step['response']


class UpdateSignaturesCronAppTestCase(IntegrationTestBase):
    def setUp(self):
        super(UpdateSignaturesCronAppTestCase, self).setUp()

        cursor = self.conn.cursor()
        # NOTE(willkg): The socorro tests don't run with the Django-managed
        # database models created in the db, so we have to do it by hand until
        # we've moved everything out of sqlalchemy/alembic land to Django land.
        #
        # FIXME(willkg): Please stop this madness soon.
        #
        # From "./manage.py sqlmigrate crashstats 0004":
        cursor.execute("""
        CREATE TABLE "crashstats_signature" (
        "id" serial NOT NULL PRIMARY KEY,
        "signature" text NOT NULL UNIQUE,
        "first_build" bigint NOT NULL,
        "first_date" timestamp with time zone NOT NULL);
        """)
        cursor.execute("""
        CREATE INDEX "crashstats_signature_signature_15c3e97d_like"
        ON "crashstats_signature" ("signature" text_pattern_ops);
        """)
        self._truncate_signatures()

    def tearDown(self):
        super(UpdateSignaturesCronAppTestCase, self).tearDown()

        cursor = self.conn.cursor()
        cursor.execute("""
        DROP TABLE crashstats_signature
        """)

        self._truncate_signatures()

    def _setup_config_manager(self):
        return super(UpdateSignaturesCronAppTestCase, self)._setup_config_manager(
            jobs_string='socorro.cron.jobs.update_signatures.UpdateSignaturesCronApp|1h'
        )

    def _truncate_signatures(self):
        """Wipe the signatures table"""
        self.conn.cursor().execute('TRUNCATE signatures CASCADE')
        self.conn.commit()

    def fetch_signatures_data(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT signature, first_build, first_report FROM signatures")
        results = cursor.fetchall()
        return [
            {
                'signature': str(result[0]),
                'first_build': str(result[1]),
                'first_report': str(result[2]),
            }
            for result in results
        ]

    def fetch_crashstats_signature_data(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT signature, first_build, first_date from crashstats_signature")
        results = cursor.fetchall()
        return [
            {
                'signature': str(result[0]),
                'first_build': str(result[1]),
                'first_date': str(result[2]),
            }
            for result in results
        ]

    def run_job_and_assert_success(self):
        # Run crontabber
        config_manager = self._setup_config_manager()
        with config_manager.context() as config:
            crontabberapp = CronTabberApp(config)
            crontabberapp.run_one('update-signatures')

        # Assert the job ran correctly
        crontabber_info = self._load_structure()
        assert crontabber_info['update-signatures']['last_error'] == {}
        assert crontabber_info['update-signatures']['last_success']

    @mock.patch('socorro.cron.jobs.update_signatures.SuperSearch')
    def test_no_crashes_to_process(self, mock_supersearch):
        supersearch = FakeModel()
        mock_supersearch.return_value = supersearch

        # Mock SuperSearch to return no results
        supersearch.add_get_step({
            'errors': [],
            'hits': [],
            'total': 0,
            'facets': {}
        })

        # Run crontabber
        self.run_job_and_assert_success()

        # Assert that nothing got inserted
        assert self.fetch_signatures_data() == []
        assert self.fetch_crashstats_signature_data() == []

    @mock.patch('socorro.cron.jobs.update_signatures.SuperSearch')
    def test_signature_insert_and_update(self, mock_supersearch):
        supersearch = FakeModel()
        mock_supersearch.return_value = supersearch

        # Verify there's nothing in the signatures table
        data = self.fetch_signatures_data()
        assert len(data) == 0

        # Mock SuperSearch to return 1 crash
        supersearch.add_get_step({
            'errors': [],
            'hits': [
                {
                    'build_id': u'20180420000000',
                    'date': u'2018-05-03T16:00:00.00000+00:00',
                    'signature': u'OOM | large'
                },
            ],
            'total': 1,
            'facets': {}
        })

        # Run crontabber
        self.run_job_and_assert_success()

        # Signature was inserted
        data = self.fetch_signatures_data()
        assert (
            data ==
            [
                {
                    'first_build': '20180420000000',
                    'first_report': '2018-05-03 16:00:00+00:00',
                    'signature': 'OOM | large',
                },
            ]
        )
        data = self.fetch_crashstats_signature_data()
        assert (
            data ==
            [
                {
                    'first_build': '20180420000000',
                    'first_date': '2018-05-03 16:00:00+00:00',
                    'signature': 'OOM | large',
                },
            ]
        )

        # Mock SuperSearch to return 1 crash with different data
        supersearch.add_get_step({
            'errors': [],
            'hits': [
                {
                    'build_id': u'20180320000000',
                    'date': u'2018-05-03T12:00:00.00000+00:00',
                    'signature': u'OOM | large'
                },
            ],
            'total': 1,
            'facets': {}
        })

        # Wipe crontabber state so we can rerun the job
        self._truncate()

        # Run crontabber again
        self.run_job_and_assert_success()

        # Signature was updated with correct data
        data = self.fetch_signatures_data()
        assert (
            data ==
            [
                {
                    'first_build': '20180320000000',
                    'first_report': '2018-05-03 12:00:00+00:00',
                    'signature': 'OOM | large',
                },
            ]
        )
        data = self.fetch_crashstats_signature_data()
        assert (
            data ==
            [
                {
                    'first_build': '20180320000000',
                    'first_date': '2018-05-03 12:00:00+00:00',
                    'signature': 'OOM | large',
                },
            ]
        )

    @mock.patch('socorro.cron.jobs.update_signatures.SuperSearch')
    def test_multiple_crash_processing(self, mock_supersearch):
        """Test processing multiple crashes with same signature"""
        supersearch = FakeModel()
        mock_supersearch.return_value = supersearch

        # Mock SuperSearch to return 4 crashes covering two signatures
        supersearch.add_get_step({
            'errors': [],
            'hits': [
                {
                    'build_id': u'20180426000000',
                    # This is the earliest date of the three
                    'date': u'2018-05-03T16:00:00.00000+00:00',
                    'signature': u'OOM | large'
                },
                {
                    # This is the earliest build id of the three
                    'build_id': u'20180322000000',
                    'date': u'2018-05-03T18:00:00.00000+00:00',
                    'signature': u'OOM | large'
                },
                {
                    'build_id': u'20180427000000',
                    'date': u'2018-05-03T19:00:00.000000+00:00',
                    'signature': u'OOM | large'
                },
                {
                    'build_id': u'20180322140748',
                    'date': u'2018-05-03T18:22:34.969718+00:00',
                    'signature': u'shutdownhang | js::DispatchTyped<T>'
                }
            ],
            'total': 4,
            'facets': {}
        })

        # Run crontabber
        self.run_job_and_assert_success()

        # Two signatures got inserted
        data = self.fetch_signatures_data()
        assert (
            sorted(data, key=lambda item: item['first_build']) ==
            [
                {
                    'first_build': '20180322000000',
                    'first_report': '2018-05-03 16:00:00+00:00',
                    'signature': 'OOM | large',
                },
                {
                    'first_build': '20180322140748',
                    'first_report': '2018-05-03 18:22:34.969718+00:00',
                    'signature': 'shutdownhang | js::DispatchTyped<T>',
                }
            ]
        )
        data = self.fetch_crashstats_signature_data()
        assert (
            sorted(data, key=lambda item: item['first_build']) ==
            [
                {
                    'first_build': '20180322000000',
                    'first_date': '2018-05-03 16:00:00+00:00',
                    'signature': 'OOM | large',
                },
                {
                    'first_build': '20180322140748',
                    'first_date': '2018-05-03 18:22:34.969718+00:00',
                    'signature': 'shutdownhang | js::DispatchTyped<T>',
                }
            ]
        )

    @mock.patch('socorro.cron.jobs.update_signatures.SuperSearch')
    def test_crash_with_no_buildid(self, mock_supersearch):
        """Test crashes with no build id are ignored"""
        supersearch = FakeModel()
        mock_supersearch.return_value = supersearch

        # Mock SuperSearch to return 1 crash with no build id
        supersearch.add_get_step({
            'errors': [],
            'hits': [
                {
                    'build_id': '',
                    'date': u'2018-05-03T16:00:00.00000+00:00',
                    'signature': u'OOM | large'
                },
            ],
            'total': 1,
            'facets': {}
        })

        # Run crontabber
        self.run_job_and_assert_success()

        # The crash has no build id, so it gets ignored and nothing gets
        # inserted
        assert self.fetch_signatures_data() == []
        assert self.fetch_crashstats_signature_data() == []
