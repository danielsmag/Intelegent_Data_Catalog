"""Include and exclude filters shared by schema and catalog profiles."""


class NameSelection:
    """Keeps names in ``include`` and drops names in ``exclude``.

    ``None`` and an empty include list keep every name.
    """

    def choose(
        self,
        names: list[str],
        include: list[str] | None,
        exclude: list[str],
    ) -> list[str]:
        """Return ``names`` in their original order after the filters."""
        allowed: set[str] | None = None if include is None else set(include)
        blocked: set[str] = set(exclude)
        chosen: list[str] = []
        name: str
        for name in names:
            if allowed is not None and len(allowed) > 0 and name not in allowed:
                continue
            if name in blocked:
                continue
            chosen.append(name)
        return chosen
