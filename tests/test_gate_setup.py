

# ── One list of gates, owned by one module (2026-08-05) ──────────────────


def test_the_cli_does_not_keep_its_own_list_of_gates():
    """`gates.BUILTIN_GATES` owns the set; the CLI derives it.

    Both files used to carry the same five-name literal *and* a comment
    calling itself the single source of truth for it. They agreed only
    because a human kept them agreeing — the property a derived name does
    not need. Asks the owning module rather than re-listing the members,
    so a gate added there and forgotten here fails instead of vanishing.
    """
    from brr import cli
    from brr import gates

    assert cli.GATES is gates.BUILTIN_GATES
    # Sanity: a rename that emptied the set would make every assertion in
    # this file pass over nothing.
    assert len(gates.BUILTIN_GATES) >= 5


