# UAT beat3 · 换窗措辞

第三拍不再用最终 markdown 里有没有「近30天」和数据集 id 来判断换窗失败。Run 完成，并且 `context_bindings` 或该 Run 的事件里带有 `time_window.label` 与 `dataset` / `dataset_id`，就算通过。假 DAS 仍要求结论里出现 30 天窗的 GMV。第一拍、第二拍的字面检查不动。

`insight_report` 的 system prompt 要求：绑定里已有 dataset 和 time_window 时，最终 markdown 原文引用 dataset（dataset_id）和 `time_window.label`。这只是提示。活模型换一种说法，不再把绑定正确的回合打成失败。

不涉及结论截断。
