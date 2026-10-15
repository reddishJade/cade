# Skills

Skills package instructions for specific tasks, such as refactoring, test generation, API integration, or database migration. Use them to share project practices with Cade.

## Skill locations

Cade discovers skills at two levels:

1. Project skills, shared through the repository:
   ```text
   <PROJECT_ROOT>/.cade/skills/<skill_name>/SKILL.md
   ```
2. User skills, shared across projects:
   ```text
   ~/.config/cade/skills/<skill_name>/SKILL.md
   ```

## Write a skill

Each skill has its own directory with a `SKILL.md` file. Include YAML metadata at the top:

```markdown
---
name: react-refactor
description: Guidelines for splitting React components, extracting hooks, and improving performance. Load when the user asks to refactor frontend components.
---

# React refactoring guidelines

When refactoring this project's components:

1. Keep each JSX file under 150 lines. Move child views to `components/` when a file exceeds that limit.
2. Encapsulate stateful network requests in `hooks/use*.ts`.
3. After each split, run `npm run test` to check for regressions.
```

## Load a skill

Cade includes summaries of discovered skills in the system prompt. When a task matches a skill's `description`, the model can load its full instructions through an internal tool.

You can also activate a skill explicitly:

```bash
/skill list                 # 查看可用技能及其描述
/skill load react-refactor  # 将指定技能加载到当前会话上下文
```
