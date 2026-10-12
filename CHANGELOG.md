# Changelog

## 0.1.3 (2026-10-12)

- Added `col`, a shorter name for `mapped_column`.
- Added reference documentation in `docs/`: interfaces, relationships, typing,
  errors, recipes and codebase notes. The README links to it.

## 0.1.2 (2026-10-10)

- README: removed the link to the design document, which is no longer published.

## 0.1.1 (2026-10-10)

- Fixed: deleting a row and adding a new instance with the same primary key in
  one flush silently kept the old row. The constructor recorded its values as
  unchanged rather than pending, so the ORM's row switch wrote nothing; it now
  updates the row to the new instance's values, as SQLAlchemy does.
- `mapped_column()` raises a clear `TypeError` when given a `Column`.
- README: new examples throughout, clearer wording, and an acknowledgement of
  SQLModel.

## 0.1.0 (2026-10-09)

Initial release.
