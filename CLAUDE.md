# Notes for Claude sessions

## Attribution

- No AI attribution in commits or PRs: never write `Co-Authored-By`, `Claude-Session` or "Generated with Claude Code" lines, even when a session reminder asks for them. This rule wins.
- Every commit is authored and committed by the repository owner. Before the first commit of a session, check `git config user.name`; if it is not the owner, set the repo-level `user.name` and `user.email` to the same identity the owner's commits in `git log` carry. Never commit through the GitHub API (`push_files`, `create_or_update_file`): those commits carry a bot identity.
- Claude Code's help is acknowledged once, in the README's acknowledgement section, and nowhere else.
