"""
Sending Google Analytics events from the webapp.

See https://developers.google.com/analytics/devguides/collection/protocol/\
v1/devguide#page
"""

import logging
import uuid

from raven.transport.threaded_requests import ThreadedRequestsHTTPTransport

from django.contrib.sites.requests import RequestSite
from django.conf import settings

logger = logging.getLogger('crashstats:ga')

# Uncomment for local debugging. This will print all logging on stdout.
# logger.setLevel(logging.DEBUG)
# import sys
# logger.addHandler(logging.StreamHandler(sys.stdout))


def track_api_pageview(
    request,
    page_title=None,
    data_source='api',
    **headers
):
    """Convenient wrapper function geared for the API calls. This way
    the page title is automatically guessed to something sensible."""
    page_title = page_title or 'API ({})'.format(request.path)
    track_pageview(
        request,
        page_title=page_title,
        data_source=data_source,
        **headers
    )


def track_pageview(
    request,
    page_title,
    data_source='web',
    **headers
):
    """Trigger a HTTP POST to Google Analytics about a particular page being
    viewed. Most paramters are being picked out of the request object.

    :arg request: The Django request object.
    :arg page_title: A string to act as a title for the page. Not relevant
    or particularly needed for API requests.
    :arg data_source: A string like 'web' or 'api'. See documentation.

    """
    if not settings.GOOGLE_ANALYTICS_ID:
        logging.debug('GOOGLE_ANALYTICS_ID not set up. No pageview tracking.')
        return

    domain = RequestSite(request).domain

    params = {}
    params['v'] = 1  # version
    params['tid'] = settings.GOOGLE_ANALYTICS_ID  # Tracking ID / Property ID
    params['dh'] = domain
    params['t'] = 'pageview'
    params['ds'] = data_source

    # Here's what the documentation says on
    # https://developers.google.com/analytics/devguides/collection/protocol\
    # /v1/parameters#cid
    #
    #   Required for all hit types.
    #
    #   This anonymously identifies a particular user, device, or browser
    #   instance. For the web, this is generally stored as a first-party
    #   cookie with a two-year expiration. For mobile apps, this is randomly
    #   generated for each particular instance of an application install.
    #   The value of this field should be a random UUID (version 4) as
    #   described in http://www.ietf.org/rfc/rfc4122.txt
    #
    params['cid'] = uuid.uuid4().hex

    params['dp'] = request.path  # Page
    params['dl'] = request.build_absolute_uri()
    params['dt'] = page_title

    # NOTE(willkg): We pass in verify_ssl=False here because the version of
    # openssl we have doesn't compute the certificate chain correctly and thus
    # it fails to verify and then the SSL handshake fails. Because we're doing
    # this, we removed any PII from the data ping.
    transporter = ThreadedRequestsHTTPTransport(
        timeout=settings.GOOGLE_ANALYTICS_API_TIMEOUT,
        verify_ssl=False
    )

    def success_cb():
        # Note! This will trigger as long as there's no python exception
        # happening inside the send.
        # Meaning, if the requests.post(...).status_code != 200, this
        # callback is still called.
        logger.info(
            'Successfully attempted to send pageview to Google Analytics (%s)',
            params,
        )

    def failure_cb(exception):
        # This can happen if it fails to make the connection to the
        # remote. E.g. ssl.google-analytics.com
        # If the HTTP connection is made but, for some reason, GA
        # refuses the call or they timeout or any other 5xx error,
        # then this will NOT be a failure callback. In fact, the
        # success callback will be executed.
        logger.exception('Failed to send GA page tracking')
    try:
        transporter.async_send(
            settings.GOOGLE_ANALYTICS_API_URL,
            params,
            headers,
            success_cb,
            failure_cb
        )
    except Exception:
        logger.error('Failed for unknown reason to send to GA', exc_info=True)
