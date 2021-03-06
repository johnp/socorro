# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from itertools import islice
import logging
import re

import ujson

from socorro import siglists
from socorro.lib.treelib import tree_get


SIGNATURE_MAX_LENGTH = 255
MAXIMUM_FRAMES_TO_CONSIDER = 40
SIGNATURE_ESCAPE_SINGLE_QUOTE = True


logger = logging.getLogger(__name__)


class Rule(object):
    """Base class for Signature generation rules"""
    def predicate(self, raw_crash, processed_crash):
        """Whether or not to run this rule

        :arg dict raw_crash: the raw crash structure
        :arg dict processed_crash: the processed crash structure

        :returns: True or False

        """
        return True

    def action(self, raw_crash, processed_crash, notes):
        """Runs the rule against the data

        This modifies ``processed_crash`` and ``notes`` in place.

        :arg dict raw_crash: the raw crash structure
        :arg dict processed_crash: the processed crash structure; modify the ``signature`` key
        :arg list notes: list to add processing notes to

        :returns: True

        """
        return True


class SignatureTool(object):
    """this is the base class for signature generation objects.  It defines the
    basic interface and provides truncation and quoting service.  Any derived
    classes should implement the '_do_generate' function.  If different
    truncation or quoting techniques are desired, then derived classes may
    override the 'generate' function.

    """

    def __init__(self, quit_check_callback=None):
        self.quit_check_callback = quit_check_callback

    def generate(self, source_list, hang_type=0, crashed_thread=None, delimiter=' | '):
        signature, signature_notes = self._do_generate(
            source_list,
            hang_type,
            crashed_thread,
            delimiter
        )
        if SIGNATURE_ESCAPE_SINGLE_QUOTE:
            signature = signature.replace("'", "''")
        if len(signature) > SIGNATURE_MAX_LENGTH:
            signature = "%s..." % signature[:SIGNATURE_MAX_LENGTH - 3]
            signature_notes.append('SignatureTool: signature truncated due to length')
        return signature, signature_notes

    def _do_generate(self, source_list, hang_type, crashed_thread, delimiter):
        raise NotImplementedError


class CSignatureTool(SignatureTool):
    """This is the class for signature generation tools that work on
    breakpad C/C++ stacks.  It provides a method to normalize signatures
    and then defines its own '_do_generate' method."""

    hang_prefixes = {
        -1: "hang",
        1: "chromehang"
    }

    def __init__(self, quit_check_callback=None):
        super(CSignatureTool, self).__init__(quit_check_callback)

        self.irrelevant_signature_re = re.compile(
            '|'.join(siglists.IRRELEVANT_SIGNATURE_RE)
        )
        self.prefix_signature_re = re.compile(
            '|'.join(siglists.PREFIX_SIGNATURE_RE)
        )
        self.signatures_with_line_numbers_re = re.compile(
            '|'.join(siglists.SIGNATURES_WITH_LINE_NUMBERS_RE)
        )
        self.trim_dll_signature_re = re.compile(
            '|'.join(siglists.TRIM_DLL_SIGNATURE_RE)
        )
        self.signature_sentinels = siglists.SIGNATURE_SENTINELS

        self.collapse_arguments = True

        self.fixup_space = re.compile(r' (?=[\*&,])')
        self.fixup_comma = re.compile(r',(?! )')

    @staticmethod
    def _is_exception(exception_list, remaining_original_line, line_up_to_current_position):
        for an_exception in exception_list:
            if remaining_original_line.startswith(an_exception):
                return True
            if line_up_to_current_position.endswith(an_exception):
                return True
        return False

    def _collapse(
        self,
        function_signature_str,
        open_string,
        replacement_open_string,
        close_string,
        replacement_close_string,
        exception_substring_list=[],
    ):
        """this method takes a string representing a C/C++ function signature
        and replaces anything between to possibly nested delimiters

        :arg list exception_substring_list: list of exceptions that shouldn't collapse

        """
        target_counter = 0
        collapsed_list = []
        exception_mode = False

        def append_if_not_in_collapse_mode(a_character):
            if not target_counter:
                collapsed_list.append(a_character)

        for index, a_character in enumerate(function_signature_str):
            if a_character == open_string:
                if self._is_exception(
                    exception_substring_list,
                    function_signature_str[index + 1:],
                    function_signature_str[:index]
                ):
                    exception_mode = True
                    append_if_not_in_collapse_mode(a_character)
                    continue
                append_if_not_in_collapse_mode(replacement_open_string)
                target_counter += 1
            elif a_character == close_string:
                if exception_mode:
                    append_if_not_in_collapse_mode(a_character)
                    exception_mode = False
                else:
                    target_counter -= 1
                    append_if_not_in_collapse_mode(replacement_close_string)
            else:
                append_if_not_in_collapse_mode(a_character)

        edited_function = ''.join(collapsed_list)
        return edited_function

    def normalize_signature(
        self,
        module=None,
        function=None,
        file=None,
        line=None,
        module_offset=None,
        offset=None,
        function_offset=None,
        normalized=None,
        **kwargs  # eat any extra kwargs passed in
    ):
        """ returns a structured conglomeration of the input parameters to
        serve as a signature.  The parameter names of this function reflect the
        exact names of the fields from the jsonMDSW frame output.  This allows
        this function to be invoked by passing a frame as **a_frame. Sometimes,
        a frame may already have a normalized version cached.  If that exists,
        return it instead.
        """
        if normalized is not None:
            return normalized
        if function:
            function = self._collapse(
                function,
                '<',
                '<',
                '>',
                'T>',
                ('name omitted', 'IPC::ParamTraits')
            )
            if self.collapse_arguments:
                function = self._collapse(
                    function,
                    '(',
                    '',
                    ')',
                    '',
                    ('anonymous namespace', 'operator')
                )

            if self.signatures_with_line_numbers_re.match(function):
                function = "%s:%s" % (function, line)
            # Remove spaces before all stars, ampersands, and commas
            function = self.fixup_space.sub('', function)
            # Ensure a space after commas
            function = self.fixup_comma.sub(', ', function)
            return function
        # if source is not None and source_line is not None:
        if file and line:
            filename = file.rstrip('/\\')
            if '\\' in filename:
                file = filename.rsplit('\\')[-1]
            else:
                file = filename.rsplit('/')[-1]
            return '%s#%s' % (file, line)
        if not module and not module_offset and offset:
            return "@%s" % offset
        if not module:
            module = ''  # might have been None
        return '%s@%s' % (module, module_offset)

    def _do_generate(self, source_list, hang_type, crashed_thread, delimiter=' | '):
        """
        each element of signatureList names a frame in the crash stack; and is:
          - a prefix of a relevant frame: Append this element to the signature
          - a relevant frame: Append this element and stop looking
          - irrelevant: Append this element only after seeing a prefix frame
        The signature is a ' | ' separated string of frame names.
        """
        signature_notes = []

        # shorten source_list to the first signatureSentinel
        sentinel_locations = []
        for a_sentinel in self.signature_sentinels:
            if type(a_sentinel) == tuple:
                a_sentinel, condition_fn = a_sentinel
                if not condition_fn(source_list):
                    continue
            try:
                sentinel_locations.append(source_list.index(a_sentinel))
            except ValueError:
                pass
        if sentinel_locations:
            source_list = source_list[min(sentinel_locations):]

        # Get all the relevant frame signatures.
        new_signature_list = []
        for a_signature in source_list:
            # If the signature matches the irrelevant signatures regex, skip to the next frame.
            if self.irrelevant_signature_re.match(a_signature):
                continue

            # If the signature matches the trim dll signatures regex, rewrite it to remove all but
            # the module name.
            if self.trim_dll_signature_re.match(a_signature):
                a_signature = a_signature.split('@')[0]

                # If this trimmed DLL signature is the same as the previous frame's, we do not want
                # to add it.
                if new_signature_list and a_signature == new_signature_list[-1]:
                    continue

            new_signature_list.append(a_signature)

            # If the signature does not match the prefix signatures regex, then it is the last one
            # we add to the list.
            if not self.prefix_signature_re.match(a_signature):
                break

        # Add a special marker for hang crash reports.
        if hang_type:
            new_signature_list.insert(0, self.hang_prefixes[hang_type])

        signature = delimiter.join(new_signature_list)

        # Handle empty signatures to explain why we failed generating them.
        if signature == '' or signature is None:
            if crashed_thread is None:
                signature_notes.append(
                    "CSignatureTool: No signature could be created because we do not know which "
                    "thread crashed"
                )
                signature = "EMPTY: no crashing thread identified"
            else:
                signature_notes.append(
                    "CSignatureTool: No proper signature could be created because no good data "
                    "for the crashing thread (%s) was found" % crashed_thread
                )
                try:
                    signature = source_list[0]
                except IndexError:
                    signature = "EMPTY: no frame data available"

        return signature, signature_notes


class JavaSignatureTool(SignatureTool):
    """This is the signature generation class for Java signatures."""

    java_line_number_killer = re.compile(r'\.java\:\d+\)$')
    java_hex_addr_killer = re.compile(r'@[0-9a-f]{8}')

    @staticmethod
    def join_ignore_empty(delimiter, list_of_strings):
        return delimiter.join(x for x in list_of_strings if x)

    def _do_generate(self, source, hang_type_unused=0, crashed_thread_unused=None, delimiter=': '):
        signature_notes = []
        try:
            source_list = [x.strip() for x in source.splitlines()]
        except AttributeError:
            signature_notes.append('JavaSignatureTool: stack trace not in expected format')
            return (
                "EMPTY: Java stack trace not in expected format",
                signature_notes
            )
        try:
            java_exception_class, description = source_list[0].split(':', 1)
            java_exception_class = java_exception_class.strip()
            # relace all hex addresses in the description by the string <addr>
            description = self.java_hex_addr_killer.sub(
                r'@<addr>',
                description
            ).strip()
        except ValueError:
            java_exception_class = source_list[0]
            description = ''
            signature_notes.append(
                'JavaSignatureTool: stack trace line 1 is not in the expected format'
            )
        try:
            java_method = re.sub(
                self.java_line_number_killer,
                '.java)',
                source_list[1]
            )
            if not java_method:
                signature_notes.append('JavaSignatureTool: stack trace line 2 is empty')
        except IndexError:
            signature_notes.append('JavaSignatureTool: stack trace line 2 is missing')
            java_method = ''

        # an error in an earlier version of this code resulted in the colon
        # being left out of the division between the description and the
        # java_method if the description didn't end with "<addr>".  This code
        # perpetuates that error while correcting the "<addr>" placement
        # when it is not at the end of the description.  See Bug 865142 for
        # a discussion of the issues.
        if description.endswith('<addr>'):
            # at which time the colon placement error is to be corrected
            # just use the following line as the replacement for this entire
            # if/else block
            signature = self.join_ignore_empty(
                delimiter,
                (java_exception_class, description, java_method)
            )
        else:
            description_java_method_phrase = self.join_ignore_empty(
                ' ',
                (description, java_method)
            )
            signature = self.join_ignore_empty(
                delimiter,
                (java_exception_class, description_java_method_phrase)
            )

        if len(signature) > SIGNATURE_MAX_LENGTH:
            signature = delimiter.join(
                (java_exception_class, java_method)
            )
            signature_notes.append(
                'JavaSignatureTool: dropped Java exception description due to length'
            )

        return signature, signature_notes


class SignatureGenerationRule(Rule):

    def __init__(self):
        super(SignatureGenerationRule, self).__init__()
        self.java_signature_tool = JavaSignatureTool()
        self.c_signature_tool = CSignatureTool()

    def _create_frame_list(self, crashing_thread_mapping, make_modules_lower_case=False):
        frame_signatures_list = []
        for a_frame in islice(
            crashing_thread_mapping.get('frames', {}),
            MAXIMUM_FRAMES_TO_CONSIDER
        ):
            if make_modules_lower_case and 'module' in a_frame:
                a_frame['module'] = a_frame['module'].lower()

            normalized_signature = self.c_signature_tool.normalize_signature(**a_frame)
            if 'normalized' not in a_frame:
                a_frame['normalized'] = normalized_signature
            frame_signatures_list.append(normalized_signature)
        return frame_signatures_list

    def _get_crashing_thread(self, processed_crash):
        return tree_get(processed_crash, 'json_dump.crash_info.crashing_thread', default=None)

    def action(self, raw_crash, processed_crash, notes):
        # If this is a Java crash, then generate a Java signature
        if raw_crash.get('JavaStackTrace', None):
            signature, signature_notes = self.java_signature_tool.generate(
                raw_crash['JavaStackTrace'],
                delimiter=': '
            )
            processed_crash['signature'] = signature
            if signature_notes:
                notes.extend(signature_notes)
            return True

        # This isn't a Java crash, so figure out what we need and then generate a C signature
        crashed_thread = self._get_crashing_thread(processed_crash)

        try:
            if processed_crash.get('hang_type', None) == 1:
                # Force the signature to come from thread 0
                signature_list = self._create_frame_list(
                    tree_get(processed_crash, 'json_dump.threads.[0]'),
                    tree_get(processed_crash, 'json_dump.system_info.os') == 'Windows NT'
                )
            elif crashed_thread is not None:
                signature_list = self._create_frame_list(
                    tree_get(processed_crash, 'json_dump.threads.[%d]' % crashed_thread),
                    tree_get(processed_crash, 'json_dump.system_info.os') == 'Windows NT'
                )
            else:
                signature_list = []
        except (KeyError, IndexError) as exc:
            notes.append('No crashing frames found because of %s' % exc)
            signature_list = []

        signature, signature_notes = self.c_signature_tool.generate(
            signature_list,
            processed_crash.get('hang_type', None),
            crashed_thread,
        )
        processed_crash['proto_signature'] = ' | '.join(signature_list)
        processed_crash['signature'] = signature
        if signature_notes:
            notes.extend(signature_notes)
        return True


class OOMSignature(Rule):
    """To satisfy Bug 1007530, this rule will modify the signature to
    tag OOM (out of memory) crashes"""

    signature_fragments = (
        'NS_ABORT_OOM',
        'mozalloc_handle_oom',
        'CrashAtUnhandlableOOM',
        'AutoEnterOOMUnsafeRegion',
        'alloc::oom::oom',
    )

    def predicate(self, raw_crash, processed_crash):
        if raw_crash.get('OOMAllocationSize'):
            return True

        signature = processed_crash.get('signature', '')
        if not signature:
            return False

        for a_signature_fragment in self.signature_fragments:
            if a_signature_fragment in signature:
                return True

        return False

    def action(self, raw_crash, processed_crash, notes):
        processed_crash['original_signature'] = processed_crash['signature']
        try:
            size = int(raw_crash['OOMAllocationSize'])
        except (TypeError, AttributeError, KeyError):
            processed_crash['signature'] = "OOM | unknown | " + processed_crash['signature']
            return True

        if size <= 262144:  # 256K
            processed_crash['signature'] = "OOM | small"
        else:
            processed_crash['signature'] = "OOM | large | " + processed_crash['signature']
        return True


class AbortSignature(Rule):
    """To satisfy Bug 803779, this rule will modify the signature to
    tag Abort crashes"""

    def predicate(self, raw_crash, processed_crash):
        return bool(raw_crash.get('AbortMessage'))

    def action(self, raw_crash, processed_crash, notes):
        processed_crash['original_signature'] = processed_crash['signature']
        abort_message = raw_crash['AbortMessage']

        if '###!!! ABORT: file ' in abort_message:
            # This is an abort message that contains no interesting
            # information. We just want to put the "Abort" marker in the
            # signature.
            processed_crash['signature'] = 'Abort | {}'.format(processed_crash['signature'])
            return True

        if '###!!! ABORT:' in abort_message:
            # Recent crash reports added some irrelevant information at the
            # beginning of the abort message. We want to remove that and keep
            # just the actual abort message.
            abort_message = abort_message.split('###!!! ABORT:', 1)[1].strip()

        if ': file ' in abort_message:
            # Abort messages contain a file name and a line number. Since
            # those are very likely to change between builds, we want to
            # remove those parts from the signature.
            abort_message = abort_message.split(': file ', 1)[0].strip()

        if len(abort_message) > 80:
            abort_message = abort_message[:77] + '...'

        processed_crash['signature'] = 'Abort | {} | {}'.format(
            abort_message,
            processed_crash['signature']
        )

        return True


class SigTrim(Rule):
    """ensure that the signature never has any leading or trailing white
    spaces"""

    def predicate(self, raw_crash, processed_crash):
        return isinstance(processed_crash.get('signature'), basestring)

    def action(self, raw_crash, processed_crash, notes):
        processed_crash['signature'] = processed_crash['signature'].strip()
        return True


class SigTrunc(Rule):
    """ensure that the signature is never longer than 255 characters"""

    def predicate(self, raw_crash, processed_crash):
        return len(processed_crash.get('signature', '')) > 255

    def action(self, raw_crash, processed_crash, notes):
        processed_crash['signature'] = "%s..." % processed_crash['signature'][:252]
        return True


class StackwalkerErrorSignatureRule(Rule):
    """ensure that the signature contains the stackwalker error message"""

    def predicate(self, raw_crash, processed_crash):
        return bool(
            processed_crash.get('signature', '').startswith('EMPTY') and
            processed_crash.get('mdsw_status_string')
        )

    def action(self, raw_crash, processed_crash, notes):
        processed_crash['signature'] = "%s; %s" % (
            processed_crash['signature'],
            processed_crash['mdsw_status_string']
        )
        return True


class SignatureRunWatchDog(SignatureGenerationRule):
    """ensure that the signature contains the stackwalker error message"""

    def predicate(self, raw_crash, processed_crash):
        return 'RunWatchdog' in processed_crash.get('signature', '')

    def _get_crashing_thread(self, processed_crash):
        # Always use thread 0 in this case, because that's the thread that
        # was hanging when the software was artificially crashed.
        return 0

    def action(self, raw_crash, processed_crash, notes):
        # For shutdownhang crashes, we need to use thread 0 instead of the
        # crashing thread. The reason is because those crashes happen
        # artificially when thread 0 gets stuck. So whatever the crashing
        # thread is, we don't care about it and only want to know what was
        # happening in thread 0 when it got stuck.
        result = super(SignatureRunWatchDog, self).action(raw_crash, processed_crash, notes)
        processed_crash['signature'] = (
            "shutdownhang | %s" % processed_crash['signature']
        )
        return result


class SignatureShutdownTimeout(Rule):
    """replaces the signature if there is a shutdown timeout message in the
    crash"""

    def predicate(self, raw_crash, processed_crash):
        return bool(raw_crash.get('AsyncShutdownTimeout'))

    def action(self, raw_crash, processed_crash, notes):
        parts = ['AsyncShutdownTimeout']
        try:
            shutdown_data = ujson.loads(raw_crash['AsyncShutdownTimeout'])
            parts.append(shutdown_data['phase'])
            conditions = [
                # NOTE(willkg): The AsyncShutdownTimeout notation condition can either be a string
                # that looks like a "name" or a dict with a "name" in it.
                #
                # This handles both variations.
                c['name'] if isinstance(c, dict) else c
                for c in shutdown_data['conditions']
            ]
            if conditions:
                conditions.sort()
                parts.append(','.join(conditions))
            else:
                parts.append("(none)")
        except (ValueError, KeyError) as exc:
            parts.append("UNKNOWN")
            notes.append('Error parsing AsyncShutdownTimeout: {}'.format(exc))

        new_sig = ' | '.join(parts)
        notes.append(
            'Signature replaced with a Shutdown Timeout signature, '
            'was: "{}"'.format(processed_crash['signature'])
        )
        processed_crash['signature'] = new_sig

        return True


class SignatureJitCategory(Rule):
    """replaces the signature if there is a JIT classification in the crash"""

    def predicate(self, raw_crash, processed_crash):
        return bool(tree_get(processed_crash, 'classifications.jit.category', default=None))

    def action(self, raw_crash, processed_crash, notes):
        notes.append(
            'Signature replaced with a JIT Crash Category, '
            'was: "{}"'.format(processed_crash.get('signature', ''))
        )
        processed_crash['signature'] = "jit | {}".format(
            tree_get(processed_crash, 'classifications.jit.category')
        )
        return True


class SignatureIPCChannelError(Rule):
    """replaces the signature if there is a IPC channel error in the crash"""

    def predicate(self, raw_crash, processed_crash):
        return bool(raw_crash.get('ipc_channel_error'))

    def action(self, raw_crash, processed_crash, notes):
        if raw_crash.get('additional_minidumps') == 'browser':
            new_sig = 'IPCError-browser | {}'
        else:
            new_sig = 'IPCError-content | {}'
        new_sig = new_sig.format(raw_crash['ipc_channel_error'][:100])

        notes.append(
            'Signature replaced with an IPC Channel Error, '
            'was: "{}"'.format(processed_crash['signature'])
        )
        processed_crash['signature'] = new_sig

        return True


class SignatureIPCMessageName(Rule):
    """augments the signature if there is a IPC message name in the crash"""

    def predicate(self, raw_crash, processed_crash):
        return bool(raw_crash.get('IPCMessageName'))

    def action(self, raw_crash, processed_crash, notes):
        processed_crash['signature'] = '{} | IPC_Message_Name={}'.format(
            processed_crash['signature'],
            raw_crash['IPCMessageName']
        )
        return True
