"""
Limits
======

Implemented according to the PhD thesis
https://www.cybertester.com/data/gruntz.pdf, which contains very thorough
descriptions of the algorithm including many examples.  We summarize here
the gist of it.

All functions are sorted according to how rapidly varying they are at
infinity using the following rules. Any two functions f and g can be
compared using the properties of L:

L=lim  log|f(x)| / log|g(x)|           (for x -> oo)

We define >, < ~ according to::

    1. f > g .... L=+-oo

        we say that:
        - f is greater than any power of g
        - f is more rapidly varying than g
        - f goes to infinity/zero faster than g

    2. f < g .... L=0

        we say that:
        - f is lower than any power of g

    3. f ~ g .... L!=0, +-oo

        we say that:
        - both f and g are bounded from above and below by suitable integral
          powers of the other

Examples
========
::
    2 < x < exp(x) < exp(x**2) < exp(exp(x))
    2 ~ 3 ~ -5
    x ~ x**2 ~ x**3 ~ 1/x ~ x**m ~ -x
    exp(x) ~ exp(-x) ~ exp(2x) ~ exp(x)**2 ~ exp(x+exp(-x))
    f ~ 1/f

So we can divide all the functions into comparability classes (x and x^2
belong to one class, exp(x) and exp(-x) belong to some other class). In
principle, we could compare any two functions, but in our algorithm, we
do not compare anything below the class 2~3~-5 (for example log(x) is
below this), so we set 2~3~-5 as the lowest comparability class.

Given the function f, we find the list of most rapidly varying (mrv set)
subexpressions of it. This list belongs to the same comparability class.
Let's say it is {exp(x), exp(2x)}. Using the rule f ~ 1/f we find an
element "w" (either from the list or a new one) from the same
comparability class which goes to zero at infinity. In our example we
set w=exp(-x) (but we could also set w=exp(-2x) or w=exp(-3x) ...). We
rewrite the mrv set using w, in our case {1/w, 1/w^2}, and substitute it
into f. Then we expand f into a series in w::

    f = c0*w^e0 + c1*w^e1 + ... + O(w^en),       where e0<e1<...<en, c0!=0

but for x->oo, lim f = lim c0*w^e0, because all the other terms go to zero,
because w goes to zero faster than the ci and ei. So::

    for e0>0, lim f = 0
    for e0<0, lim f = +-oo   (the sign depends on the sign of c0)
    for e0=0, lim f = lim c0

We need to recursively compute limits at several places of the algorithm, but
as is shown in the PhD thesis, it always finishes.

Important functions from the implementation:

compare(a, b, x) compares "a" and "b" by computing the limit L.
mrv(e, x) returns list of most rapidly varying (mrv) subexpressions of "e"
rewrite(e, Omega, x, wsym) rewrites "e" in terms of w
leadterm(f, x) returns the lowest power term in the series of f
mrv_leadterm(e, x) returns the lead term (c0, e0) for e
limitinf(e, x) computes lim e  (for x->oo)
limit(e, z, z0) computes any limit by converting it to the case x->oo

All the functions are really simple and straightforward except
rewrite(), which is the most difficult/complex part of the algorithm.
When the algorithm fails, the bugs are usually in the series expansion
(i.e. in SymPy) or in rewrite.

This code is almost exact rewrite of the Maple code inside the Gruntz
thesis.

Debugging
---------

Because the gruntz algorithm is highly recursive, it's difficult to
figure out what went wrong inside a debugger. Instead, turn on nice
debug prints by defining the environment variable SYMPY_DEBUG. For
example:

[user@localhost]: SYMPY_DEBUG=True ./bin/isympy

In [1]: limit(sin(x)/x, x, 0)
limitinf(_x*sin(1/_x), _x) = 1
+-mrv_leadterm(_x*sin(1/_x), _x) = (1, 0)
| +-mrv(_x*sin(1/_x), _x) = set([_x])
| | +-mrv(_x, _x) = set([_x])
| | +-mrv(sin(1/_x), _x) = set([_x])
| |   +-mrv(1/_x, _x) = set([_x])
| |     +-mrv(_x, _x) = set([_x])
| +-mrv_leadterm(exp(_x)*sin(exp(-_x)), _x, set([exp(_x)])) = (1, 0)
|   +-rewrite(exp(_x)*sin(exp(-_x)), set([exp(_x)]), _x, _w) = (1/_w*sin(_w), -_x)
|     +-sign(_x, _x) = 1
|     +-mrv_leadterm(1, _x) = (1, 0)
+-sign(0, _x) = 0
+-limitinf(1, _x) = 1

And check manually which line is wrong. Then go to the source code and
debug this function to figure out the exact problem.

"""
from functools import reduce

from sympy.core import Basic, S, Mul, PoleError, expand_mul, evaluate, Integer
from sympy.core.cache import cacheit
from sympy.core.numbers import I, oo
from sympy.core.symbol import Dummy, Wild, Symbol
from sympy.core.traversal import bottom_up
from sympy.core.sorting import ordered

from sympy.functions import log, exp, sign, sin
from sympy.series.order import Order
from sympy.utilities.exceptions import SymPyDeprecationWarning
from sympy.utilities.misc import debug_decorator as debug
from sympy.utilities.timeutils import timethis

def mrv(e, x):
    """
    Calculate the MRV set of the expression.

    Examples
    ========

    >>> mrv(log(x - log(x))/log(x), x)
    {x}

    """

    if e == x:
        return {x}
    elif e.is_Integer:
        return {}
    elif e.is_Mul or e.is_Add:
        a, b = e.as_two_terms()
        ans1 = mrv(a, x)
        ans2 = mrv(b, x)
        return mrv_max(mrv(a, x), mrv(b, x), x)
    elif e.is_Pow:
        return mrv(e.base, x)
    elif e.is_Function:
        return reduce(lambda a, b: mrv_max(a, b, x), (mrv(a, x) for a in e.args))
    raise NotImplementedError(f"Can't calculate the MRV of {e}.")

def mrv_max(f, g, x):
    """Compute the maximum of two MRV sets.

    Examples
    ========

    >>> mrv_max({log(x)}, {x**5}, x)
    {x**5}

    """

    if not f:
        return g
    if not g:
        return f
    if f & g:
        return f | g

def rewrite(e, x, w):
    r"""
    Rewrites the expression in terms of the MRV subexpression.

    Parameters
    ==========

    e : Expr
        an expression
    x : Symbol
        variable of the `e`
    w : Symbol
        The symbol which is going to be used for substitution in place
        of the MRV in `x` subexpression.

    Returns
    =======

    The rewritten expression

    Examples
    ========

    >>> rewrite(exp(x)*log(x), x, y)
    (log(x)/y, -x)

    """

    Omega = mrv(e, x)

    if x in Omega:
        # Moving up in the asymptotical scale:
        with evaluate(False):
            e = e.subs(x, exp(x))
            Omega = {s.subs(x, exp(x)) for s in Omega}

    Omega = list(ordered(Omega, keys=lambda a: -len(mrv(a, x))))

    for g in Omega:
        sig = signinf(g.exp, x)
        if sig not in (1, -1):
            raise NotImplementedError(f'Result depends on the sign of {sig}.')

    if sig == 1:
        w = 1/w  # if g goes to oo, substitute 1/w

    # Rewrite and substitute subexpressions in the Omega.
    for a in Omega:
        c = limitinf(a.exp/g.exp, x)
        b = exp(a.exp - c*g.exp)*w**c  # exponential must never be expanded here
        with evaluate(False):
            e = e.xreplace({a: b})

    return e

@cacheit
def mrv_leadterm(e, x):
    """
    Compute the leading term of the series.

    Returns
    =======

    tuple
        The leading term `c_0 w^{e_0}` of the series of `e` in terms
        of the most rapidly varying subexpression `w` in form of
        the pair ``(c0, e0)`` of Expr.

    Examples
    ========

    >>> leadterm(1/exp(-x + exp(-x)) - exp(x), x)
    (-1, 0)

    """

    w = Dummy('w', real=True, positive=True)
    e = rewrite(e, x, w)
    return e.leadterm(w)

@cacheit
def signinf(e, x):
    r"""
    Determine sign of the expression at the infinity.

    Returns
    =======

    {1, 0, -1}
        One or minus one, if `e > 0` or `e < 0` for `x` sufficiently
        large and zero if `e` is *constantly* zero for `x\to\infty`.

    """

    if not e.has(x):
        return sign(e)
    if e == x or (e.is_Pow and signinf(e.base, x) == 1):
        return S(1)

@cacheit
def limitinf(e, x):
    """
    Compute the limit of the expression at the infinity.

    Examples
    ========

    >>> limitinf(exp(x)*(exp(1/x - exp(-x)) - exp(1/x)), x)
    -1

    """

    if not e.has(x):
        return e

    c0, e0 = mrv_leadterm(e, x)
    sig = signinf(e0, x)
    if sig == 1:
        return Integer(0)
    if sig == -1:
        return signinf(c0, x)*oo
    if sig == 0:
        return limitinf(c0, x)
    raise NotImplementedError(f'Result depends on the sign of {sig}.')


def gruntz(e, z, z0, dir="+"):
    """
    Compute the limit of e(z) at the point z0 using the Gruntz algorithm.

    Explanation
    ===========

    ``z0`` can be any expression, including oo and -oo.

    For ``dir="+"`` (default) it calculates the limit from the right
    (z->z0+) and for ``dir="-"`` the limit from the left (z->z0-). For infinite z0
    (oo or -oo), the dir argument does not matter.

    This algorithm is fully described in the module docstring in the gruntz.py
    file. It relies heavily on the series expansion. Most frequently, gruntz()
    is only used if the faster limit() function (which uses heuristics) fails.
    """

    if str(dir) == "-":
        e0 = e.subs(z, z0 - 1/z)
    elif str(dir) == "+":
        e0 = e.subs(z, z0 + 1/z)
    else:
        raise NotImplementedError("dir must be '+' or '-'")

    r = limitinf(e0, z)
    return r

# tests
x = Symbol('x')
# Print the basic limit:
print(gruntz(sin(x)/x, x, 0))

# Test other cases
assert gruntz(sin(x)/x, x, 0) == 1
assert gruntz(2*sin(x)/x, x, 0) == 2
assert gruntz(sin(2*x)/x, x, 0) == 2
assert gruntz(sin(x)**2/x, x, 0) == 0
assert gruntz(sin(x)/x**2, x, 0) == oo
assert gruntz(sin(x)**2/x**2, x, 0) == 1
assert gruntz(sin(sin(sin(x)))/sin(x), x, 0) == 1
assert gruntz(2*log(x+1)/x, x, 0) == 2
assert gruntz(sin((log(x+1)/x)*x)/x, x, 0) == 1
