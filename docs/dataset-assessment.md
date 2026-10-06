# Local Fixture Assessment

The optional `pr_diff_100.jsonl` file contains 100 synthetic PR records:

- 80 validation records and 20 holdout records;
- 10 generated repositories with repeated templates;
- 40 records with one expected finding and 60 clean records;
- 21 rule IDs across security and reliability categories;
- before/after files, unified diff, expected location, severity and repair patterns.

It is suitable for deterministic contract checks: diff parsing, line localization, risk-surface routing, structured Finding validation and policy replay plumbing. It should not be used as the project's only benchmark because the generator metadata, repeated repository templates and known injected labels create strong synthetic shortcuts.

The file remains local and is excluded from commits. Future evaluation should add repository-level splits from real or expert-reviewed PRs and hard negatives before reporting recall, false-positive rate or cost improvements.
