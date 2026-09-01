# 临时验证：SiliconFlow 流式 usage 行为（验证后删除）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from app.core.config import settings

llm = ChatOpenAI(
    base_url=settings.LLM_API_BASE or None,
    api_key=settings.LLM_API_KEY,
    model=settings.LLM_MODEL,
    streaming=True,
    temperature=0.3,
    stream_usage=True,
)

msgs = [HumanMessage(content="用一句话介绍年假")]
usage_seen = []
text = ""
for chunk in llm.stream(msgs):
    if chunk.content:
        text += chunk.content if isinstance(chunk.content, str) else ""
    if chunk.usage_metadata:
        usage_seen.append(chunk.usage_metadata)

print("chunks with usage:", len(usage_seen))
for u in usage_seen:
    print("  usage:", u)
print("answer_len:", len(text))

# 对照：非流式 usage
llm2 = ChatOpenAI(
    base_url=settings.LLM_API_BASE or None,
    api_key=settings.LLM_API_KEY,
    model=settings.LLM_MODEL,
    streaming=False,
    temperature=0.3,
)
resp = llm2.invoke(msgs)
print("non-stream usage:", resp.usage_metadata)
