# 提示词

把 [enhance_prompt.md](enhance_prompt.md) 交给一个强模型，把「拆解这支 Skill 并做成讲解视频」扩成制作简报。

替换三个占位符：

- `{{USER_INSTRUCTION}}`：原始需求
- `{{SKILL_INVENTORY}}`：只读清单（目录树、frontmatter、章节标题、短摘录）。还没摘录时写 `尚未提供，制作端必须先只读阅读 Skill 目录`
- `{{ASSETS_JSON}}`：素材 JSON，没有则写 `[]`

整份填好的模板作为**一条**用户消息发送。不要把目标 Skill 的脚本交给模型执行。

```python
import json
from pathlib import Path

instruction = "拆解 skills/skill-dissect-video，讲清它怎么写、怎么调用、有没有风险，做成讲解视频。"
inventory = Path("inventory.md").read_text(encoding="utf-8")
assets = []
template = Path("prompts/enhance_prompt.md").read_text(encoding="utf-8")
message = (
    template.replace("{{USER_INSTRUCTION}}", instruction)
    .replace("{{SKILL_INVENTORY}}", inventory)
    .replace("{{ASSETS_JSON}}", json.dumps(assets, ensure_ascii=False))
)
Path("request.md").write_text(message, encoding="utf-8")
```

模型应只返回 JSON。保存为 `response.json` 后提取简报：

```python
result = json.loads(Path("response.json").read_text(encoding="utf-8"))
if result["is_video_request"]:
    Path("dense_prompt.md").write_text(result["dense_prompt"], encoding="utf-8")
```

把 `dense_prompt.md` 和 Skill 目录交给已加载 `skill-dissect-video` 的 Agent。制作阶段仍然只读目标 Skill，并先跑 `scripts/scan_skill.py`。
