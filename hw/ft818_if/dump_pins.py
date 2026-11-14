import sys
from sexpr import load_symbol, pins
D = r"C:/Program Files/KiCad/9.0/share/kicad/symbols"
for spec in sys.argv[1:]:
    lib, name = spec.split(":")
    print(spec)
    for k, v in sorted(pins(load_symbol(D, lib, name)).items()):
        print("  ", k, v)
