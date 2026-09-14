"""The outbox as a verb table — move 4 of the daemon rewrite.

``shapes`` (the typed seam) · ``notices`` (the one notice writer) · ``verbs``
(one handler per frontmatter key) · ``table`` (key → handler, in precedence) ·
``drain`` (the composition ``daemon._drain_outbox`` calls) · ``land`` and
``fold`` (the first frame-owned verbs).

Deliberately empty of imports: ``daemon`` imports ``notices`` at module load,
and ``verbs``/``table``/``drain`` import ``daemon`` — nothing here may pull
them in eagerly.
"""
