# Zoom Meeting Transcriber

这个文件夹只做两件事：

1. 把 OBS Studio 录好的 Zoom 会议视频转成 Markdown 文字稿。
2. 把文字稿总结成会议纪要。

依赖运行在 `wolf` virtualenv 里（`workon wolf`），脚本会自动切换，不需要手动配置。

## 1. 转文字稿

1. 用 OBS Studio 录制 Zoom 会议。
2. 把视频放到 `recordings/`。
3. 运行：

```bash
./scripts/transcribe_recording.sh
```

输出只有一个文件：

```text
recordings/视频名.transcript.md
```

固定使用最好的模型 `large-v3`，会议语言固定为日语 (`ja`)，GPU (`cuda`) 推理。也可以直接指定文件：

```bash
./scripts/transcribe_recording.sh recordings/meeting.mp4
```

## 2. 总结会议

把生成的 `*.transcript.md` 贴到 [prompts/meeting_summary_prompt.md](./prompts/meeting_summary_prompt.md) 里，让 AI 总结。
