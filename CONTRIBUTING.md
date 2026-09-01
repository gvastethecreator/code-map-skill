# Contributing

Maintain Code Map is a portable Agent Skills package. Keep changes agent-agnostic unless a host-specific file is explicitly additive.

## Before opening a pull request

1. Run `python scripts/validate_package.py`.
2. Run `python -B -m unittest SKILLS/maintain-code-map/scripts/test_codemap_tool.py`.
3. Update the nearest behavioral case in `evals/cases.json` when the process contract changes.
4. Keep public documentation and examples in American English.
5. Do not bake machine-local drive letters, user paths, or private checkout names into the skill.

Do not weaken an assertion to make a behavior change pass. Explain any platform-specific limitation in the pull request.

