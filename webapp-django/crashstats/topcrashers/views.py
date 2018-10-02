import datetime
from collections import defaultdict

from django import http
from django.conf import settings
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import urlquote
from six import text_type

from session_csrf import anonymous_csrf

from crashstats.base.utils import get_comparison_signatures, SignatureStats
from crashstats.crashstats import models
from crashstats.crashstats.decorators import (
    check_days_parameter,
    pass_default_context,
)
from crashstats.supersearch.models import SuperSearchUnredacted
from crashstats.supersearch.utils import get_date_boundaries
from crashstats.topcrashers.forms import TopCrashersForm


def datetime_to_build_id(date):
    """Return a build_id-like string from a datetime. """
    return date.strftime('%Y%m%d%H%M%S')


def get_topcrashers_stats(**kwargs):
    """Return the results of a search. """
    params = kwargs
    range_type = params.pop('_range_type')
    dates = get_date_boundaries(params)

    params['_aggs.signature'] = [
        'platform',
        'is_garbage_collecting',
        'hang_type',
        'process_type',
        'startup_crash',
        '_histogram.uptime',
        '_cardinality.install_time',
    ]
    params['_histogram_interval.uptime'] = 60

    # We don't care about no results, only facets.
    params['_results_number'] = 0

    if params.get('process_type') in ('any', 'all'):
        params['process_type'] = None

    if range_type == 'build':
        params['build_id'] = [
            '>=' + datetime_to_build_id(dates[0]),
            '<' + datetime_to_build_id(dates[1])
        ]

    api = SuperSearchUnredacted()
    search_results = api.get(**params)

    signatures_stats = []
    if search_results['total'] > 0:
        # Run the same query but for the previous date range, so we can
        # compare the rankings and show rank changes.
        delta = (dates[1] - dates[0]) * 2
        params['date'] = [
            '>=' + (dates[1] - delta).isoformat(),
            '<' + dates[0].isoformat()
        ]
        params['_aggs.signature'] = [
            'platform',
        ]
        params['_facets_size'] *= 2

        if range_type == 'build':
            params['date'][1] = '<' + dates[1].isoformat()
            params['build_id'] = [
                '>=' + datetime_to_build_id(dates[1] - delta),
                '<' + datetime_to_build_id(dates[0])
            ]

        previous_range_results = api.get(**params)
        previous_signatures = get_comparison_signatures(previous_range_results)

        for index, signature in enumerate(search_results['facets']['signature']):
            previous_signature = previous_signatures.get(signature['term'])
            signatures_stats.append(SignatureStats(
                signature=signature,
                num_total_crashes=search_results['total'],
                rank=index,
                platforms=models.Platforms().get_all()['hits'],
                previous_signature=previous_signature,
            ))
    return signatures_stats


@pass_default_context
@anonymous_csrf
@check_days_parameter([1, 3, 7, 14, 28], default=7)
def topcrashers(request, days=None, possible_days=None, default_context=None):
    context = default_context or {}

    form = TopCrashersForm(request.GET)
    if not form.is_valid():
        return http.HttpResponseBadRequest(text_type(form.errors))

    product = form.cleaned_data['product']
    versions = form.cleaned_data['version']
    crash_type = form.cleaned_data['process_type']
    os_name = form.cleaned_data['platform']
    result_count = form.cleaned_data['_facets_size']
    tcbs_mode = form.cleaned_data['_tcbs_mode']
    range_type = form.cleaned_data['_range_type']

    range_type = 'build' if range_type == 'build' else 'report'

    if not tcbs_mode or tcbs_mode not in ('realtime', 'byday'):
        tcbs_mode = 'realtime'

    if product not in context['products']:
        return http.HttpResponseBadRequest('Unrecognized product')

    context['product'] = product

    if not versions:
        # :(
        # simulate what the nav.js does which is to take the latest version
        # for this product.
        for pv in context['active_versions'][product]:
            if pv['is_featured']:
                url = '%s&version=%s' % (
                    request.build_absolute_uri(), urlquote(pv['version'])
                )
                return redirect(url)
        if context['active_versions'][product]:
            # Not a single version was featured, but there were active
            # versions. In this case, use the first available
            # *active* version.
            for pv in context['active_versions'][product]:
                url = '%s&version=%s' % (
                    request.build_absolute_uri(), urlquote(pv['version'])
                )
                return redirect(url)

    # See if all versions support builds. If not, refuse to show the "by build"
    # range option in the UI.
    versions_have_builds = True
    for version in versions:
        for pv in context['active_versions'][product]:
            if pv['version'] == version and not pv['has_builds']:
                versions_have_builds = False
                break

    context['versions_have_builds'] = versions_have_builds

    # Used to pick a version in the dropdown menu.
    context['version'] = versions[0] if versions else ''

    if tcbs_mode == 'realtime':
        end_date = timezone.now().replace(microsecond=0)
    elif tcbs_mode == 'byday':
        end_date = timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # settings.PROCESS_TYPES might contain tuple to indicate that some
    # are actual labels.
    process_types = []
    for option in settings.PROCESS_TYPES:
        if isinstance(option, (list, tuple)):
            process_types.append(option[0])
        else:
            process_types.append(option)
    if crash_type not in process_types:
        crash_type = 'browser'

    context['crash_type'] = crash_type

    os_api = models.Platforms()
    operating_systems = os_api.get()
    if os_name not in (os_['name'] for os_ in operating_systems):
        os_name = None

    context['os_name'] = os_name

    # set the result counts filter in the context to use in
    # the template. This way we avoid hardcoding it twice and
    # have it defined in one common location.
    context['result_counts'] = settings.TCBS_RESULT_COUNTS
    if result_count not in context['result_counts']:
        result_count = settings.TCBS_RESULT_COUNTS[0]

    context['result_count'] = result_count
    context['query'] = {
        'product': product,
        'versions': versions,
        'crash_type': crash_type,
        'os_name': os_name,
        'result_count': text_type(result_count),
        'mode': tcbs_mode,
        'range_type': range_type,
        'end_date': end_date,
        'start_date': end_date - datetime.timedelta(days=days),
    }

    topcrashers_stats = get_topcrashers_stats(
        product=product,
        version=versions,
        platform=os_name,
        process_type=crash_type,
        date=[
            '<' + end_date.isoformat(),
            '>=' + context['query']['start_date'].isoformat()
        ],
        _facets_size=result_count,
        _range_type=range_type,
    )

    count_of_included_crashes = 0
    signatures = []

    for topcrashers_stats_item in topcrashers_stats[:int(result_count)]:
        signatures.append(topcrashers_stats_item.signature_term)
        count_of_included_crashes += topcrashers_stats_item.num_crashes

    context['number_of_crashes'] = count_of_included_crashes
    context['total_percentage'] = len(topcrashers_stats) and (
        100.0 * count_of_included_crashes / len(topcrashers_stats)
    )

    # Get augmented bugs data.
    bugs = defaultdict(list)
    if signatures:
        bugs_api = models.Bugs()
        for b in bugs_api.get(signatures=signatures)['hits']:
            bugs[b['signature']].append(b['id'])

    # Get augmented signature data.
    sig_date_data = {}
    if signatures:
        sig_api = models.SignatureFirstDate()
        # SignatureFirstDate().get_dates() is an optimized version
        # of SignatureFirstDate().get() that returns a dict of
        # signature --> dates.
        first_dates = sig_api.get_dates(signatures)
        for signature_term, dates in first_dates.items():
            sig_date_data[signature_term] = dates['first_date']

    for topcrashers_stats_item in topcrashers_stats:
        crash_counts = []
        # Due to the inconsistencies of OS usage and naming of
        # codes and props for operating systems the hacky bit below
        # is required. Socorro and the world will be a better place
        # once https://bugzilla.mozilla.org/show_bug.cgi?id=790642 lands.
        for operating_system in operating_systems:
            if operating_system['name'] == 'Unknown':
                # not applicable in this context
                continue
            os_code = operating_system['code'][0:3].lower()
            key = '%s_count' % os_code
            crash_counts.append([topcrashers_stats_item.num_crashes_per_platform[key],
                                operating_system['name']])

        signature_term = topcrashers_stats_item.signature_term
        # Augment with bugs.
        if signature_term in bugs:
            if hasattr(topcrashers_stats_item, 'bugs'):
                topcrashers_stats_item.bugs.extend(bugs[signature_term])
            else:
                topcrashers_stats_item.bugs = bugs[signature_term]

        # Augment with first appearance dates.
        if signature_term in sig_date_data:
            topcrashers_stats_item.first_report = sig_date_data[signature_term]

        if hasattr(topcrashers_stats_item, 'bugs'):
            topcrashers_stats_item.bugs.sort(reverse=True)

    context['topcrashers_stats'] = topcrashers_stats
    context['days'] = days
    context['report'] = 'topcrasher'
    context['possible_days'] = possible_days
    context['total_crashing_signatures'] = len(signatures)
    context['total_number_of_crashes'] = len(topcrashers_stats)
    context['process_type_values'] = []
    for option in settings.PROCESS_TYPES:
        if option == 'all':
            continue
        if isinstance(option, (list, tuple)):
            value, label = option
        else:
            value = option
            label = option.capitalize()
        context['process_type_values'].append((value, label))

    context['platform_values'] = settings.DISPLAY_OS_NAMES

    return render(request, 'topcrashers/topcrashers.html', context)
