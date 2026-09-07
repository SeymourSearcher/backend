# backend

Tests: `scripts/run_tests.sh` (add `--docker` to run against the pinned
dependency set, which is the run that counts). See `tests/README.md`.

## Plan

users upload export files
log each upload
option for if this is adding to db or overriding
we follow a github branch like structure to make modifying this easy

when you upload litterally just store users hexcode 
use delta E CIE76 for deltaE

sorting features for similar to a hex
find closest matches

auction snipe / tracker for seymour items

things that make value:
Cool hex code: #123456, #654321
Duplicate pairs
Hex codes close to skyblock armour sets
repeating hex: #E3E3E3
2 digit hex: #0000CE
sets with triple AxByCz on all pieces (A,B,C same)
AxBxCx sets
AABBCC pieces
all 1,2,3,4,5,6 letter words
eg 4 letter, repeat 
99MEND99
9MEND999

0-1.999 t1
2-4.999 t2
5-9.999 t3

## Catalog

`app/static/data.json` mirrors the SeymourAnalyzer mod's checklist -- it is the
maintained list of Seymour target colours, so we take its names, hexes,
categories and page order rather than curating our own.
https://github.com/Schnerry/SeymourAnalyzerFabric

Resync with `scripts/sync_checklist.py` (bump `UPSTREAM_REF` first); it prints
the follow-up steps. `sets` is ours, not upstream's, and is carried across
renames rather than regenerated -- new upstream items are reported, not
auto-assigned.

needed things to display
landing page - show dashboard at slight tilt

after sign in -> dashboard
dashboard shows total seymours all users

side bar tabs to
add seymours
need for sets
view your collection bar at top to view others, sets tab, findable sets (sets that could be created using other users seymours)

