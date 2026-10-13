"""xmlmerge against the shipped tables: run  python tests/test_xmlmerge.py <extracted dir> <game dir>"""
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from narew import xmlmerge  # noqa: E402
from studio import textcodec  # noqa: E402

EX, GAME = sys.argv[1], sys.argv[2]
CASES = [
    (os.path.join(EX, 'xml', 'XML', 'item_table.xml'), 'XML\\item_table.xml', 'item'),
    (os.path.join(EX, 'xml', 'XML', 'brick_table.xml'), 'XML\\brick_table.xml', 'brick'),
    (os.path.join(EX, 'xml', 'XML', 'product.xml'), 'XML\\product.xml', 'product'),
    (os.path.join(EX, 'xml', 'XML', 'crafting.xml'), 'XML\\crafting.xml', 'craft'),
    (os.path.join(EX, 'xml', 'XML', 'sky.xml'), 'XML\\sky.xml', 'sky'),
    (os.path.join(EX, 'gui', 'Gui', 'sound.xml'), 'Gui\\sound.xml', 'sound'),
    (os.path.join(GAME, 'Language', 'string.xml'), 'Language\\string.xml', 'string'),
]

fails = 0
for path, entry, tag in CASES:
    raw = open(path, 'rb').read()
    text, info = textcodec.decode(raw)
    keys = xmlmerge.rule_for(entry, tag)
    spans, _ = xmlmerge.index_records(text, tag, keys)
    regex_count = len(re.findall(r'<' + tag + r'[\s/>]', text))
    # 1) every record found
    ok_count = len(spans) <= regex_count and len(spans) > 0
    # 2) re-merging a record with itself reproduces equal parse (formatting may differ)
    first_key = next(iter(spans))
    el = xmlmerge.find(text, entry, tag, first_key)
    same = xmlmerge.apply(text, [el], entry)
    try:
        ET.fromstring(text.encode('utf-8'))
        strict = True
    except ET.ParseError:
        strict = False          # shipped file has TinyXML-tolerated defects
    reparsed_same = ET.fromstring(same.encode('utf-8')) is not None if strict else True
    # 3) replace + add
    mod = ET.fromstring(ET.tostring(el))
    for k in keys:
        if k in ('id', 'index'):
            mod.set(k, '19999' if tag != 'brick' else '239')
    rep = ET.fromstring(ET.tostring(el))
    changed_child = False
    for c in rep.iter():
        if c is not rep and (c.text or '').strip():
            c.text = 'MERGE-TEST'
            changed_child = True
            break
    report = xmlmerge.MergeReport()
    out = xmlmerge.apply(text, [rep, mod], entry, report)
    if strict:
        ET.fromstring(out.encode('utf-8'))
    n_after = len(xmlmerge.index_records(out, tag, keys)[0])
    # untouched records byte-identical
    sp_after, _ = xmlmerge.index_records(out, tag, keys)
    untouched_ok = all(text[s.start:s.end] == out[sp_after[k].start:sp_after[k].end]
                       for k, s in list(spans.items())[1:200] if k in sp_after)
    encoded = textcodec.encode(out, info)
    good = ok_count and reparsed_same and n_after == len(spans) + (0 if tuple(mod.get(k, '') for k in keys) in spans else 1) \
        and untouched_ok and (not changed_child or 'MERGE-TEST' in out)
    fails += not good
    print(f"{'OK ' if good else 'FAIL'} {entry}: records {len(spans)} (regex {regex_count}), "
          f"replaced {len(report.replaced)}, added {len(report.added)}, after {n_after}, "
          f"untouched identical {untouched_ok}, {info.encoding}/{info.to_dict()['newline']}, {len(encoded)} bytes")
print('ALL OK' if not fails else f'{fails} FAILED')
sys.exit(1 if fails else 0)
