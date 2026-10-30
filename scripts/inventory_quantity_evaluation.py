"""Compare inventory totals independently of purchased package counts."""
import math

UNITS = {'g':('mass',1), 'kg':('mass',1000), 'mg':('mass',.001),
         'ml':('volume',1), 'l':('volume',1000), '개':('count',1)}


def total_equal(actual_quantity, actual_unit, expected):
    def normalized(quantity, unit):
        if isinstance(quantity,bool) or not isinstance(quantity,(int,float)) or not math.isfinite(quantity) or quantity <= 0:
            return None
        spec=UNITS.get(str(unit).lower())
        return (spec[0],quantity*spec[1]) if spec else None
    a=normalized(actual_quantity,actual_unit)
    b=normalized(expected.get('quantity'),expected.get('unit'))
    return bool(a and b and a[0]==b[0] and math.isclose(a[1],b[1],rel_tol=1e-6,abs_tol=1e-6))
