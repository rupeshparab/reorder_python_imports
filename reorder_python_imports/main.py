from __future__ import absolute_import
from __future__ import print_function
from __future__ import unicode_literals

import argparse
import ast
import collections
import io
import tokenize
from difflib import unified_diff

import six
from aspy.refactor_imports.import_obj import import_obj_from_str
from aspy.refactor_imports.sort import sort


class CodeType(object):
    PRE_IMPORT_CODE = 'pre_import_code'
    IMPORT = 'import'
    NON_CODE = 'non_code'
    CODE = 'code'


CodePartition = collections.namedtuple('CodePartition', ('code_type', 'src'))


TERMINATES_COMMENT = frozenset((tokenize.NL, tokenize.ENDMARKER))
TERMINATES_DOCSTRING = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))
TERMINATES_IMPORT = frozenset((tokenize.NEWLINE, tokenize.ENDMARKER))
TERMINATES_CODE = frozenset((tokenize.ENDMARKER,))


class TopLevelImportVisitor(ast.NodeVisitor):
    def __init__(self):
        self.top_level_import_line_numbers = []

    def _visit_import(self, node):
        # If it's indented, we don't really care about the import.
        if node.col_offset == 0:
            self.top_level_import_line_numbers.append(node.lineno)

    visit_Import = visit_ImportFrom = _visit_import


def get_line_offsets_by_line_no(src):
    # Padded so we can index with line number
    offsets = [None, 0]
    for line in src.splitlines():
        offsets.append(offsets[-1] + len(line) + 1)
    return offsets


def partition_source(src):
    """Partitions source into a list of `CodePartition`s for import
    refactoring.
    """
    # pylint:disable=too-many-locals
    if type(src) is not six.text_type:
        raise TypeError('Expected text but got `{0}`'.format(type(src)))

    # In python2, ast.parse(text_string_with_encoding_pragma) raises
    # SyntaxError: encoding declaration in Unicode string
    # We'll encode arbitrarily to UTF-8, though it's incorrect in some cases
    # for things like strings and comments, we're really only looking for the
    # start token for imports, which are ascii.
    ast_obj = ast.parse(src.encode('UTF-8'))
    visitor = TopLevelImportVisitor()
    visitor.visit(ast_obj)

    line_offsets = get_line_offsets_by_line_no(src)

    chunks = []
    startpos = 0
    pending_chunk_type = None
    possible_ending_tokens = None
    seen_import = False
    for (
            token_type, _, (srow, scol), (erow, ecol), _,
    ) in tokenize.generate_tokens(io.StringIO(src).readline):
        # Searching for a start of a chunk
        if pending_chunk_type is None:
            if not seen_import and token_type == tokenize.COMMENT:
                pending_chunk_type = CodeType.PRE_IMPORT_CODE
                possible_ending_tokens = TERMINATES_COMMENT
            elif not seen_import and token_type == tokenize.STRING:
                pending_chunk_type = CodeType.PRE_IMPORT_CODE
                possible_ending_tokens = TERMINATES_DOCSTRING
            elif scol == 0 and srow in visitor.top_level_import_line_numbers:
                seen_import = True
                pending_chunk_type = CodeType.IMPORT
                possible_ending_tokens = TERMINATES_IMPORT
            elif token_type == tokenize.NL:
                # A NL token is a non-important newline, we'll immediately
                # append a NON_CODE partition
                endpos = line_offsets[erow] + ecol
                srctext = src[startpos:endpos]
                startpos = endpos
                chunks.append(CodePartition(CodeType.NON_CODE, srctext))
            elif token_type == tokenize.COMMENT:
                pending_chunk_type = CodeType.CODE
                possible_ending_tokens = TERMINATES_COMMENT
            elif token_type == tokenize.ENDMARKER:
                # Token ended right before end of file or file was empty
                pass
            else:
                pending_chunk_type = CodeType.CODE
                possible_ending_tokens = TERMINATES_CODE
        # Attempt to find ending of token
        elif token_type in possible_ending_tokens:
            endpos = line_offsets[erow] + ecol
            srctext = src[startpos:endpos]
            startpos = endpos
            chunks.append(CodePartition(pending_chunk_type, srctext))
            pending_chunk_type = None
            possible_ending_tokens = None

    # Make sure we're not removing any code
    assert ''.join(partition.src for partition in chunks) == src
    return chunks


def separate_comma_imports(partitions):
    """Turns `import a, b` into `import a` and `import b`"""
    def _inner():
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                import_obj = import_obj_from_str(partition.src)
                if import_obj.has_multiple_imports:
                    for new_import_obj in import_obj.split_imports():
                        yield CodePartition(
                            CodeType.IMPORT, new_import_obj.to_text()
                        )
                else:
                    yield partition
            else:
                yield partition

    return list(_inner())


def remove_duplicated_imports(partitions):
    def _inner():
        seen = set()
        for partition in partitions:
            if partition.code_type is CodeType.IMPORT:
                import_obj = import_obj_from_str(partition.src)
                if import_obj not in seen:
                    seen.add(import_obj)
                    yield partition
            else:
                yield partition
    return list(_inner())


def apply_import_sorting(partitions):
    pre_import_code = []
    imports = []
    trash = []
    rest = []
    for partition in partitions:
        {
            CodeType.PRE_IMPORT_CODE: pre_import_code,
            CodeType.IMPORT: imports,
            CodeType.NON_CODE: trash,
            CodeType.CODE: rest,
        }[partition.code_type].append(partition)

    import_obj_to_partition = dict(
        (import_obj_from_str(partition.src), partition)
        for partition in imports
    )

    new_imports = []
    sorted_blocks = sort(import_obj_to_partition.keys())
    for block in sorted_blocks:
        for import_obj in block:
            new_imports.append(import_obj_to_partition[import_obj])
        new_imports.append(CodePartition(CodeType.NON_CODE, '\n'))
    # XXX: I want something like [x].join(...) (like str join) but for now
    # this works
    if new_imports:
        new_imports.pop()

    # There's the potential that we moved a bunch of whitespace onto the
    # beginning of the rest of the code.  To fix this, we're going to combine
    # all of that code, and then make sure there are two linebreaks to start
    restsrc = ''.join(partition.src for partition in rest)
    restsrc = restsrc.lstrip('\n').rstrip()
    if restsrc:
        rest = [
            CodePartition(CodeType.NON_CODE, '\n\n'),
            CodePartition(CodeType.CODE, restsrc + '\n'),
        ]
    else:
        rest = []

    return pre_import_code + new_imports + rest


STEPS = (
    separate_comma_imports,
    remove_duplicated_imports,
    apply_import_sorting,
)


def fix_file_contents(contents):
    partitioned = partition_source(contents)
    for step in STEPS:
        partitioned = step(partitioned)
    return ''.join(part.src for part in partitioned)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('filenames', nargs='*')
    parser.add_argument('--show-diff', action='store_true')
    args = parser.parse_args(argv)

    retv = 0
    for filename in args.filenames:
        contents = io.open(filename).read()
        new_contents = fix_file_contents(contents)
        if contents != new_contents:
            retv = 1
            if args.show_diff:
                print('Showing diff for {0}'.format(filename))
                diff = unified_diff(
                    contents.split('\n'),
                    new_contents.split('\n'),
                    fromfile=filename,
                    tofile=filename,
                )
                for line in diff:
                    print(line)
            else:
                print('Reordering imports in {0}'.format(filename))
                with io.open(filename, 'w') as f:
                    f.write(new_contents)

    return retv


if __name__ == '__main__':
    exit(main())
