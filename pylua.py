import cStringIO
import sys
import os
import ast
import subprocess

lua_exe = '~/src/luajit-2.0/src/luajit'
lua_exe = os.path.normpath(os.path.expanduser(lua_exe))

# a modified version of the function from ast.py, with an optional "whitespace"
# argument
def dump(node, annotate_fields=True, include_attributes=False, whitespace=False):
    """
    Return a formatted dump of the tree in *node*.  This is mainly useful for
    debugging purposes.  The returned string will show the names and the values
    for fields.  This makes the code impossible to evaluate, so if evaluation is
    wanted *annotate_fields* must be set to False.  Attributes such as line
    numbers and column offsets are not dumped by default.  If this is wanted,
    *include_attributes* can be set to True.
    """
    def _format(node, indent=0):
        sp = ('  ' * (indent+1)) if whitespace else ''
        nl = '\n' if whitespace else ''

        if isinstance(node, ast.AST):
            fields = [(a, _format(b, indent+1)) for a, b in ast.iter_fields(node)]
            rv = '%s(%s%s%s' % (node.__class__.__name__, nl, sp, ', '.join(
                ('%s=%s' % field for field in fields)
                if annotate_fields else
                (b for a, b in fields)
            ))
            if include_attributes and node._attributes:
                rv += fields and ', ' or ' '
                rv += ', '.join('%s=%s' % (a, _format(getattr(node, a), indent+1))
                                for a in node._attributes)
            return rv + ')'
        elif isinstance(node, list):
            return '[%s%s%s]' % (nl, sp, ', '.join(_format(x, indent+1) for x in node))
        return repr(node)
    if not isinstance(node, ast.AST):
        raise TypeError('expected AST, got %r' % node.__class__.__name__)
    return _format(node)

class PyLua(ast.NodeVisitor):
    def __init__(self):
        self.stream = cStringIO.StringIO()
        self.indentation = 0

    def visit_all(self, nodes):
        for node in nodes:
            self.visit(node)

    def visit_all_sep(self, nodes, sep):
        first = True
        for node in nodes:
            if first:
                first = False
            else:
                self.emit(sep)
            self.visit(node)

    def visit_or(self, node, orelse):
        if node:
            self.visit(node)
        else:
            self.emit(orelse)

    def visit(self, node):
        super(PyLua, self).visit(node)

    def visit_Print(self, node):
        self.emit('print(')
        self.generic_visit(node)
        self.emit(')')

    def visit_Num(self, node):
        self.emit(repr(node.n))

    def visit_Add(self, node):
        self.emit('+')
    def visit_Mult(self, node):
        self.emit('*')
    def visit_Div(self, node):
        self.emit('/')
    def visit_Sub(self, node):
        self.emit('-')

    def visit_Return(self, node):
        self.indent()
        self.emit('return ')
        self.generic_visit(node)
        self.eol()

    def visit_FunctionDef(self, node):
        v = dict(body='foo')
        v.update(**vars(node))

        self.emit('\n')
        self.indent()
        self.emit('%(name)s = function(' % v)
        self.visit(node.args)
        self.emit(')\n')

        self.push_scope()
        self.visit_all(node.body)
        self.pop_scope()

        #self.emit('\n')
        self.indent()
        self.emit('end\n')

    def visit_Dict(self, node):
        self.emit('{ ')
        for k,v in zip(node.keys, node.values):
            # TODO: optimize pretty keys
            self.emit('[')
            self.visit(k)
            self.emit(']=')
            self.visit(v)
            self.emit(', ')
        self.emit('}')

    def visit_List(self, node):
        self.emit('{')
        self.visit_all_sep(node.elts, ', ')
        self.emit('}')

    def visit_arguments(self, node):
        self.visit_all_sep(node.args, ', ')
        # FIXME: kwargs, ...

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Pow):
            self.emit('math.pow(')
            self.visit(node.left)
            self.emit(', ')
            self.visit(node.right)
            self.emit(')')
        elif isinstance(node.op, ast.Mod):
            self.emit('pylua.mod(')
            self.visit(node.left)
            self.emit(', ')
            self.visit(node.right)
            self.emit(')')
        else:
            self.emit_paren_maybe(node, node.left, '(')
            self.visit(node.left)
            self.emit_paren_maybe(node, node.left, ')')
            self.visit(node.op)
            self.emit_paren_maybe(node, node.right, '(')
            self.visit(node.right)
            self.emit_paren_maybe(node, node.right, ')')

    def visit_BoolOp(self, node):
        first = True
        for x in node.values:
            if first:
                first = False
            else:
                self.visit(node.op)
            self.emit_paren_maybe(node, x, '(')
            self.visit(x)
            self.emit_paren_maybe(node, x, ')')

    def visit_UnaryOp(self, node):
        self.visit(node.op)
        self.visit(node.operand)

    def visit_Not(self, node):
        self.emit(' not ')
    def visit_USub(self, node):
        self.emit('-')

    def visit_IfExp(self, node):
        # FIXME here and in similar: resolve parentheses and priorities!
        self.visit(node.test)
        self.emit(' and ')
        self.visit(node.body)
        self.emit(' or ')
        self.visit(node.orelse)

    def visit_Call(self, node):
        self.visit(node.func)
        self.emit('(')
        first = True
        if len(node.keywords)>0:
            first = False
            self.emit('pylua.keywords{')
            self.visit_all_sep(node.keywords, ', ')
            self.emit('}')
        if len(node.args)>0:
            if first:
                first = False
            else:
                self.emit(', ')
            self.visit_all_sep(node.args, ', ')
        self.emit(')')
    def visit_keyword(self, node):
        self.emit(node.arg)
        self.emit('=')
        self.visit(node.value)

    def visit_Compare(self, node):
        self.visit(node.left)
        self.visit_all(node.ops)
        self.visit_all(node.comparators)

    def visit_Subscript(self, node):
        if isinstance(node.slice, ast.Index):
            self.visit(node.value)
            self.emit('[')
            if isinstance(node.slice.value, ast.Num):
                self.emit('%d' % (node.slice.value.n + 1))
            else:
                self.visit(node.slice)
            self.emit(']')
        elif isinstance(node.slice, ast.Slice):
            # TODO: pylua.slice because other for string vs. table
            self.emit('pylua.slice(')
            self.visit(node.value)
            self.emit(', ')
            self.visit_or(node.slice.lower, 'nil')
            self.emit(', ')
            self.visit_or(node.slice.upper, 'nil')
            if node.slice.step:
                self.emit(', ')
                self.visit(node.step)
            self.emit(')')
        else:
            self.emit('[ ? ]')

    def visit_Tuple(self, node):
        #self.emit('{')
        self.visit_all_sep(node.elts, ', ')
        #self.emit('}')

    def visit_Name(self, node):
        if node.id == 'None':
            self.emit('nil')
        elif node.id == 'True':
            self.emit('true')
        elif node.id == 'False':
            self.emit('false')
        else:
            self.emit(node.id)

    def visit_Assign(self, node):
        self.indent()
        for x in node.targets:
            self.visit(x)
            self.emit(' ')
        self.emit('= ')
        self.visit(node.value)
        self.eol()

    def visit_AugAssign(self, node):
        self.indent()
        self.visit(node.target)
        self.emit(' = ')
        self.visit(node.target)
        self.visit(node.op)
        self.visit(node.value)
        self.eol()

    def visit_Expr(self, node):
        self.indent()
        if isinstance(node.value, ast.Str):
            self.emit('-- ')
            self.emit(node.value.s)
        else:
            self.visit(node.value)
        self.eol()  # TODO: yes, or no?

    def visit_If(self, node):
        self.indent()
        self.emit('if ')
        def test_plus_body(self, node):
            self.visit(node.test)
            self.emit(' then\n')

            self.push_scope()
            self.visit_all(node.body)
            self.pop_scope()

            if node.orelse:
                if len(node.orelse)==1 and isinstance(node.orelse[0], ast.If):
                    # optimize elif into 'elseif'
                    self.indent()
                    self.emit('elseif ')
                    test_plus_body(self, node.orelse[0])
                else:
                    self.indent()
                    self.emit('else\n')
                    self.push_scope()
                    self.visit_all(node.orelse)
                    self.pop_scope()
        test_plus_body(self, node)

        self.indent()
        self.emit('end\n')

    def visit_For(self, node):
        self.indent()
        if node.target and node.iter:
            self.emit('for ')
            self.visit(node.target)
            self.emit(' in ipairs(')
            self.visit(node.iter)
            self.emit(') do\n')

            self.push_scope()
            self.visit_all(node.body)
            self.pop_scope()

            self.indent()
            self.emit('end\n')
        else:
            self.emit('FOR ... ?\n')

    def visit_Continue(self, node):
        # FIXME LATER
        self.indent()
        self.emit('goto continue\n')

    def visit_ListComp(self, node):
        # FIXME LATER
        self.emit('pylua.COMPREHENSION()')

    def visit_Compare(self, node):
        if len(node.ops)==1 and isinstance(node.ops[0], ast.NotIn):
            self.emit('pylua.op_not_in(')
            self.visit(node.left)
            self.emit(', ')
            self.visit_all_sep(node.comparators, ', ')
            self.emit(')')
        elif len(node.ops)==1 and isinstance(node.ops[0], ast.In):
            self.emit('pylua.op_in(')
            self.visit(node.left)
            self.emit(', ')
            self.visit_all_sep(node.comparators, ', ')
            self.emit(')')
        elif len(node.ops)==1 and isinstance(node.ops[0], ast.Is):
            self.emit('pylua.op_is(')
            self.visit(node.left)
            self.emit(', ')
            self.visit_all_sep(node.comparators, ', ')
            self.emit(')')
        elif len(node.ops)==1 and isinstance(node.ops[0], ast.IsNot):
            self.emit('pylua.op_is_not(')
            self.visit(node.left)
            self.emit(', ')
            self.visit_all_sep(node.comparators, ', ')
            self.emit(')')
        else:
            self.visit(node.left)
            self.visit_all(node.ops)
            self.visit_all(node.comparators)

    def visit_Lt(self, node):
        self.emit('<')
    def visit_LtE(self, node):
        self.emit('<=')
    def visit_Gt(self, node):
        self.emit('>')
    def visit_GtE(self, node):
        self.emit('>=')
    def visit_Eq(self, node):
        self.emit('==')
    def visit_NotEq(self, node):
        self.emit('~=')

    def visit_And(self, node):
        self.emit(' and ')
    def visit_Or(self, node):
        self.emit(' or ')

    def visit_Attribute(self, node):
        if node.attr in ['join']:
            self.emit('pylua.str_maybe(')
            self.visit(node.value)
            self.emit(')')
        else:
            self.visit(node.value)
        self.emit('.')
        self.emit(node.attr)

    def visit_Str(self, node):
        self.emit("'")
        # FIXME: better escaping of strings
        self.emit(node.s.encode('utf8').encode('string_escape'))
        #self.emit(node.s.replace('\\', '\\\\').replace('"', '\\"'))
        self.emit("'")

    def push_scope(self):
        self.indentation += 1
    def pop_scope(self):
        self.indentation -= 1

    def emit_paren_maybe(self, parent, child, text):
        if isinstance(parent, ast.BinOp) and isinstance(child, ast.BinOp) and \
                (isinstance(parent.op, ast.Mult) or isinstance(parent.op, ast.Div)) and \
                (isinstance(child.op, ast.Add) or isinstance(child.op, ast.Sub)):
            self.emit(text)
            return
        if isinstance(parent, ast.BinOp) and isinstance(child, ast.BoolOp):
            self.emit(text)
            return
        if isinstance(parent, ast.BoolOp) and isinstance(child, ast.BoolOp) and \
                isinstance(parent.op, ast.And) and isinstance(child.op, ast.Or):
            self.emit(text)
            return
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not) and \
                isinstance(child, ast.BoolOp):
            self.emit(text)
            return

    def indent(self):
        self.emit('  '*self.indentation)
    def eol(self):
        self.emit('\n')

    def emit(self, val):
        self.stream.write(val)

_dump_ast=dump
def run_file(filename, dump=False):
    contents = open(filename, 'rU').read()
    if not contents.endswith('\n'):
        contents += '\n'

    tree = ast.parse(contents, filename)

    visitor = PyLua()
    visitor.visit(tree)

    lua_program = visitor.stream.getvalue()
    if dump:
        print _dump_ast(tree, include_attributes=True, whitespace=True)
    #    print '-'*80
    #    print lua_program
    #    print '-'*80
    #else:
    #    return runjit(lua_program)
    return runjit(lua_program)

def main():
    filename = sys.argv[1]
    print run_file(filename, True)

def runjit(program):
    filename = '_pylua_temp.lua'
    open(filename, 'wb').write(program)
    #try:
    #    args = [lua_exe, filename]
    #    process = subprocess.Popen(args, stdout = subprocess.PIPE)
    #    stdout, stderr = process.communicate()
    #finally:
    #    os.remove(filename)

    #return stdout

if __name__ == '__main__':
    main()

