# Source-of-truth checklist content.
# Each group has an id, a display title, and an ordered list of item texts.
# Item ids are derived as "<group_id>-<index>" and used as state keys.

GROUPS = [
    {"section": "Outside", "id": "yardwork", "title": "Yardwork", "items": [
        "Weeding", "Rocks", "Front yard leaves", "Flowers in front planters",
        "Cut sod border", "Replace lightbulbs",
    ]},
    {"section": "Outside", "id": "patio", "title": "Patio", "items": [
        "Wipe down furniture", "Put covers on cushions", "Relocate toolboxes/grill",
        "Sweep or powerwash concrete", "Clean fans/check for wasps",
        "Firepit \u2192 wood", "Firepit \u2192 s'mores", "Firepit \u2192 table(s)/wipe down",
        "Decorations?", "Music?", "Tablecloths?",
    ]},
    {"section": "House", "id": "kitchen", "title": "Kitchen", "items": [
        "Counter quadrant 1: coffee maker", "Counter quadrant 2: toaster/stove",
        "Sink/windowsill", "Counter quadrant 3: under cabinets", "Table",
        "Dog toys/food", "Move big plants downstairs", "General clutter",
        "Clear space downstairs", "Table for plants", "Relocate board games",
    ]},
    {"section": "House", "id": "floorA", "title": "Floor Side Quest \u2014 Option A", "items": [
        "Redo flooring",
    ]},
    {"section": "House", "id": "floorB", "title": "Floor Side Quest \u2014 Option B", "items": [
        "Don't redo flooring",
    ]},
    {"section": "House", "id": "floorEither", "title": "Floor Side Quest \u2014 Either way", "items": [
        "Vacuum", "Mop", "Wash or replace back rug",
    ]},
    {"section": "House", "id": "livingroom", "title": "Living Room", "items": [
        "Glass coffee table", "Little table clutter", "Clear off chairs",
        "Dust TV, art, piano, legos",
    ]},
    {"section": "House", "id": "bathrooms", "title": "Bathrooms", "items": [
        "Toilets", "Sinks", "Refill soap/toilet paper", "Wipe mirrors",
    ]},
    {"section": "House", "id": "milo", "title": "Milo", "items": [
        "Brush/bath/brush again", "Dental treats",
    ]},
]


def all_item_ids():
    ids = []
    for group in GROUPS:
        for idx in range(len(group["items"])):
            ids.append(f"{group['id']}-{idx}")
    return ids
