"""The game's rules: kinds, recipes and the numbers behind them.

Import the submodules (`bobgame_rules.items`, `.recipes`, `.entities`,
`.body`, `.clock`, `.social`, `.terrain`) rather than adding names here; this
module only exists so `import bobgame_rules` gives them all.
"""

from . import body, clock, entities, items, recipes, social, terrain

__all__ = ["body", "clock", "entities", "items", "recipes", "social", "terrain"]
