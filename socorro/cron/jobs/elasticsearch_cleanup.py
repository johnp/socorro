# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from configman import Namespace
from configman.converters import class_converter

from socorro.cron.base import BaseCronApp


class ElasticsearchCleanupCronApp(BaseCronApp):
    """Delete old Elasticsearch indices"""

    app_name = 'elasticsearch-cleanup'
    app_version = '1.0'
    app_description = 'Delete old Elasticsearch indices'

    required_config = Namespace()
    required_config.add_option(
        'elasticsearch_cleaner_class',
        default='socorro.external.es.index_cleaner.IndexCleaner',
        doc='a class that handles the deletion of obsolete indices',
        from_string_converter=class_converter,
    )

    def run(self):
        cleaner = self.config.elasticsearch_cleaner_class(self.config)
        cleaner.delete_old_indices()
