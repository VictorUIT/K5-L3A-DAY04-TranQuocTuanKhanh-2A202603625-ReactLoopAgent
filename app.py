import json
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from agent import SYSTEM_PROMPT, run_agent
from providers import Provider
from status_tools import SCHEMAS, StatusTools

load_dotenv()

ROOT = Path(__file__).resolve().parent
PROVIDER_OPTIONS = {
    "Anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "OpenAI": ("openai", "OPENAI_API_KEY"),
    "Gemini": ("gemini", "GEMINI_API_KEY"),
    "OpenRouter": ("openrouter", "OPENROUTER_API_KEY"),
}
MODES = ["Mock (không cần key)", "Live (gọi LLM thật)", "Replay eval JSON"]
REPORT_FILES = ["eval_before.json", "eval_after.json"]

st.set_page_config(
    page_title="ReactLoopAgent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --panel: #151b2b;
        --panel-raised: #1c2438;
        --border: rgba(148, 163, 184, .17);
        --muted: #9aa8bf;
        --accent: #e879b9;
    }
    .stApp {
        background:
            radial-gradient(ellipse at 86% 0%, rgba(86, 74, 133, .18), transparent 34%),
            #0d1220;
        color: #e8edf7;
    }
    [data-testid="stSidebar"] {
        background: #111827;
        border-right: 1px solid var(--border);
    }
    [data-testid="stSidebar"] > div:first-child { padding-top: 1.2rem; }
    [data-testid="stChatMessage"] {
        background: rgba(21, 27, 43, .76);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 1rem 1.1rem;
        margin-bottom: .8rem;
    }
    [data-testid="stChatInput"] {
        background: #151b2b;
        border: 1px solid var(--border);
        border-radius: 14px;
    }
    [data-testid="stMetric"] {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: .8rem 1rem;
    }
    [data-testid="stExpander"] {
        background: rgba(21, 27, 43, .56);
        border: 1px solid var(--border);
        border-radius: 12px;
    }
    .app-subtitle { color: var(--muted); margin-top: -.5rem; }
    .mode-note { color: var(--muted); font-size: .86rem; line-height: 1.5; }
    .status-pill {
        display: inline-block;
        padding: .25rem .65rem;
        border: 1px solid rgba(232, 121, 185, .36);
        border-radius: 999px;
        color: #f3add5;
        background: rgba(232, 121, 185, .09);
        font-size: .8rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


class MockProvider:
    """Deterministic offline provider for demonstrating the agent/tool loop."""

    def __init__(self, mode):
        self.mode = mode

    def complete(self, messages, schemas):
        if self.mode == "Provider: HTTP 503":
            raise RuntimeError("Lỗi provider giả lập: HTTP 503.")

        question = next(
            (message["content"] for message in messages if message["role"] == "user"),
            "",
        )
        lowered = question.casefold()
        providers = []
        if any(word in lowered for word in ("github", "actions", "pages")):
            providers.append("github")
        if any(word in lowered for word in ("cloudflare", "spectrum", "cache purge", "workers")):
            providers.append("cloudflare")
        if not providers:
            providers = ["github", "cloudflare"]

        tool_results = []
        for message in messages:
            if message["role"] == "tool":
                try:
                    tool_results.append(json.loads(message["content"]))
                except json.JSONDecodeError:
                    continue
        completed_providers = {
            result.get("provider")
            for result in tool_results
            if result.get("provider") in providers
        }
        pending = [name for name in providers if name not in completed_providers]

        if pending:
            provider_name = pending[0]
            component = next(
                (name for name in ("Actions", "Pages", "Spectrum", "Cache Purge", "Workers")
                 if name.casefold() in lowered),
                "",
            )
            arguments = {"provider": provider_name}
            if component:
                arguments["component"] = component
            return self._tool_call(
                f"mock-{len(tool_results) + 1}",
                "get_status",
                arguments,
            )

        summary = []
        for result in tool_results:
            provider_name = result.get("provider", "provider")
            if result.get("ok") is False:
                summary.append(
                    f"- {provider_name}: không lấy được dữ liệu ({result.get('error', 'lỗi tool')})."
                )
                continue
            overall = result.get("overall", {})
            description = overall.get("description", "không có mô tả trạng thái")
            captured = result.get("captured_at", "không rõ thời điểm")
            incidents = result.get("incidents", [])
            summary.append(
                f"- {provider_name}: {description}; snapshot `{captured}`."
            )
            if incidents:
                summary.extend(
                    f"  - {incident.get('name')} — {incident.get('status')} "
                    f"({incident.get('updated_at')})"
                    for incident in incidents[:3]
                )
            elif result.get("matching_incidents_in_last_50") == 0:
                summary.append("  - Không thấy kết quả khớp trong phạm vi tìm kiếm của tool.")
            if result.get("source"):
                summary.append(f"  - Nguồn: {result['source']}")

        answer = (
            "Đây là phản hồi **Mock** được tạo theo kịch bản offline, không phải câu trả lời từ LLM.\n\n"
            + "\n".join(summary)
            + "\n\nDữ liệu snapshot chỉ minh họa luồng; hãy dùng chế độ Live để hỏi model thật."
        )
        return {
            "message": {"role": "assistant", "content": answer},
            "calls": [],
            "finish": "stop",
            "usage": {},
        }

    @staticmethod
    def _tool_call(call_id, name, arguments):
        raw_arguments = json.dumps(arguments, ensure_ascii=False)
        return {
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw_arguments},
                }],
            },
            "calls": [{"id": call_id, "name": name, "arguments": raw_arguments}],
            "finish": "tool_calls",
            "usage": {},
        }


class DemoTools:
    def __init__(self, mode, error_mode):
        self.delegate = StatusTools(mode=mode)
        self.schemas = SCHEMAS
        self.system_prompt = SYSTEM_PROMPT
        self.error_mode = error_mode
        self.injected = False

    def execute(self, name, arguments):
        if self.error_mode == "Tool: timeout" and not self.injected:
            self.injected = True
            return {
                "ok": False,
                "error": "TimeoutError",
                "message": "Timeout giả lập trong giao diện demo.",
                "provider": arguments.get("provider"),
            }
        return self.delegate.execute(name, arguments)


def render_trace(trace, usage=None):
    for event in trace:
        if event.get("event") == "step":
            title = (
                f"Step {event.get('step')} · model call · "
                f"finish: {event.get('finish') or 'unknown'}"
            )
            with st.expander(title, expanded=False):
                st.caption("Model step; không lưu chain-of-thought.")
        elif event.get("event") == "tool":
            title = f"Step {event.get('step')} · tool: {event.get('name')}"
            with st.expander(title, expanded=False):
                st.markdown("**Arguments**")
                st.json(event.get("arguments", {}))
                st.markdown("**Result**")
                st.json(event.get("result", {}))
        else:
            with st.expander(
                f"Step {event.get('step', '?')} · {event.get('event', 'event')}",
                expanded=False,
            ):
                st.json(event)
    if usage is not None:
        with st.expander("Usage"):
            st.json(usage)


def render_replay():
    st.subheader("Replay eval JSON")
    uploaded = st.file_uploader("Tải báo cáo eval JSON", type=["json"])
    report_path = st.selectbox("Hoặc chọn báo cáo trong project", REPORT_FILES)
    try:
        if uploaded is not None:
            report = json.loads(uploaded.getvalue().decode("utf-8"))
            report_name = uploaded.name
        else:
            path = ROOT / report_path
            if not path.is_file():
                st.info(f"Chưa có `{report_path}`. Hãy tải lên báo cáo JSON.")
                return
            report = json.loads(path.read_text(encoding="utf-8"))
            report_name = report_path
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        st.error(f"Không đọc được báo cáo JSON: {exc}")
        return

    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        st.error("File JSON không có danh sách `cases` hợp lệ.")
        return

    summary = report.get("summary", {})
    st.caption(f"Đang xem `{report_name}`")
    metric_cols = st.columns(3)
    metric_cols[0].metric("Điểm", summary.get("score_0_100", "—"))
    metric_cols[1].metric(
        "Đã chấm",
        f"{summary.get('scored_cases', '—')}/{summary.get('total_cases', len(cases))}",
    )
    metric_cols[2].metric("Chưa chấm", summary.get("unscored_cases", "—"))

    labels = [f"{case.get('id', 'case')} · {case.get('question', '')[:90]}" for case in cases]
    selected = st.selectbox(
        "Chọn case",
        range(len(cases)),
        format_func=lambda index: labels[index],
    )
    case = cases[selected]
    result = case.get("result", {})
    grade = case.get("grade", {})
    st.markdown(f"**Câu hỏi:** {case.get('question', '')}")
    answer_col, grade_col = st.columns([2, 1])
    with answer_col:
        st.markdown("#### Câu trả lời")
        st.markdown(result.get("answer") or "_Không có câu trả lời._")
    with grade_col:
        st.metric("Điểm case", grade.get("score", "Unscored"))
        st.caption(grade.get("reason", ""))
    st.markdown("#### Trace")
    render_trace(result.get("trace", []), result.get("usage", []))


with st.sidebar:
    st.markdown("### 🤖 ReactLoopAgent")
    st.caption("Trợ lý điều tra trạng thái GitHub & Cloudflare")
    mode_label = st.radio("Chế độ", MODES, index=0)

    source_mode = "snapshot"
    max_steps = 6
    error_mode = "Không"
    provider_name = "anthropic"
    model_name = os.getenv("MODEL", "")
    api_key_name = "ANTHROPIC_API_KEY"

    if mode_label != "Replay eval JSON":
        max_steps = st.slider("max_steps", min_value=1, max_value=16, value=6)
        source_mode = st.selectbox("Dữ liệu tool", ["snapshot", "live"])
        if mode_label == "Mock (không cần key)":
            error_mode = st.selectbox(
                "Chèn lỗi giả lập",
                ["Không", "Tool: timeout", "Provider: HTTP 503"],
            )
            st.caption("Mock chạy offline bằng kịch bản cố định; không gọi LLM.")
        else:
            provider_labels = list(PROVIDER_OPTIONS)
            default_provider = os.getenv("PROVIDER", "anthropic")
            default_index = next(
                (index for index, label in enumerate(provider_labels)
                 if PROVIDER_OPTIONS[label][0] == default_provider),
                0,
            )
            provider_label = st.selectbox(
                "Provider", provider_labels, index=default_index
            )
            provider_name, api_key_name = PROVIDER_OPTIONS[provider_label]
            model_name = st.text_input("Model", value=os.getenv("MODEL", ""))
            st.caption(
                f"API key: {'đã cấu hình' if os.getenv(api_key_name) else 'chưa cấu hình'} "
                f"({api_key_name}); giá trị được giữ ẩn."
            )
            st.caption("Live sử dụng API thật và có thể phát sinh chi phí.")

        if st.button("🗑️ Xóa hội thoại", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

    st.markdown("---")
    st.markdown(
        '<div class="mode-note">Mỗi câu hỏi là một lần gọi độc lập tới '
        '<code>run_agent</code>; agent không nhận lịch sử hội thoại trước đó.</div>',
        unsafe_allow_html=True,
    )

st.title("ReactLoopAgent")
st.markdown(
    '<div class="app-subtitle">Theo dõi từng bước model → tool → kết quả.</div>',
    unsafe_allow_html=True,
)
st.markdown(
    f'<span class="status-pill">{mode_label}</span>',
    unsafe_allow_html=True,
)
st.write("")

if mode_label == "Replay eval JSON":
    render_replay()
else:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["content"])
            elif message.get("status") == "error":
                st.error(message["content"])
            elif message.get("status") == "max_steps":
                st.warning(message["content"])
            else:
                st.markdown(message["content"])
            if message.get("trace") is not None:
                render_trace(message["trace"], message.get("usage", []))

    prompt = st.chat_input("Hỏi về trạng thái GitHub hoặc Cloudflare...")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            try:
                with st.spinner("Agent đang kiểm tra trạng thái và gọi tools..."):
                    if mode_label == "Mock (không cần key)":
                        active_provider = MockProvider(error_mode)
                        active_tools = DemoTools(source_mode, error_mode)
                    else:
                        active_provider = Provider(provider_name, model_name)
                        active_tools = StatusTools(mode=source_mode)
                    result = run_agent(
                        prompt,
                        active_provider,
                        active_tools,
                        max_steps=max_steps,
                    )

                status = result["status"]
                if status == "completed":
                    content = result.get("answer") or "Agent hoàn tất nhưng không có nội dung."
                    st.markdown(content)
                elif status == "max_steps":
                    content = result.get("answer") or "Agent đã đạt giới hạn max_steps."
                    st.warning(content)
                else:
                    content = result.get("error", "Agent gặp lỗi khi xử lý yêu cầu.")
                    st.error(content)

                stored = {
                    "role": "assistant",
                    "content": content,
                    "status": status,
                    "trace": result.get("trace", []),
                    "usage": result.get("usage", []),
                }
                st.session_state.messages.append(stored)
                render_trace(stored["trace"], stored["usage"])
            except (ValueError, RuntimeError) as exc:
                content = str(exc)
                st.error(content)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": content,
                    "status": "error",
                })
