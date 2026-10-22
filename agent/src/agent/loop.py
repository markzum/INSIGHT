"""Цикл plan → act → analyze → verify → revise."""

from __future__ import annotations

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from agent.bank_index import BankIndex
from agent.clients import ClientDirectory
from agent.context import Session, ToolContext
from agent.errors import BudgetExceeded, ClientNotFound, InsightNotFound, ToolDenied
from agent.llm_client import Budget, LLMClient, grounded_chat, grounded_draft
from agent.news_model import NewsCluster
from agent.news_store import NewsStore
from agent.planner import coerce_plan, guess_indicator, plan_messages
from agent.profile_features import Profile, build_profile
from agent.prompts import PROMPT_VERSION, load_prompt, wrap_untrusted
from agent.ranking import RelevanceScore, SensitivityRules, priority_score, score_relevance, select_top
from agent.registry import ToolRegistry
from agent.schemas.models import schema_errors
from agent.settings import Settings
from agent.tools import build_registry
from agent.trace import TraceStore
from agent.verifier import check_chat, check_insight, next_action

_HINTS = {
    "ceo": {"loan", "deposit", "guarantee", "leasing", "factoring", "rko", "fx_hedge", "payroll"},
    "cfo": {"loan", "deposit", "guarantee", "leasing", "factoring", "rko", "fx_hedge", "payroll"},
    "treasurer": {"loan", "deposit", "guarantee", "factoring", "fx_hedge"},
}


class AgentRuntime:
    def __init__(
        self,
        settings: Settings,
        directory: ClientDirectory,
        news: NewsStore,
        bank: BankIndex,
        rules: SensitivityRules,
        llm: LLMClient,
        tools: ToolRegistry,
        traces: TraceStore,
    ) -> None:
        self.settings = settings
        self.directory = directory
        self.news = news
        self.bank = bank
        self.rules = rules
        self.llm = llm
        self.tools = tools
        self.traces = traces

    @classmethod
    def build(cls, settings: Settings) -> "AgentRuntime":
        return cls(
            settings=settings,
            directory=ClientDirectory(settings.clients_csv, settings.payments_csv, settings.persons_csv),
            news=NewsStore.load(settings.news_path),
            bank=BankIndex.load(settings.bank_docs_dir),
            rules=SensitivityRules.load(),
            llm=LLMClient(settings),
            tools=build_registry(),
            traces=TraceStore(settings.db_path),
        )

    def run_insights(
        self,
        client_id: str,
        period_from: str,
        period_to: str,
        top_n: int | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.directory.get(client_id) is None:
            raise ClientNotFound(client_id)
        profile = build_profile(self.directory, client_id)
        limit = top_n or self.settings.top_n
        pairs = []
        for cluster in self.news.clusters:
            if not cluster.in_period(period_from, period_to):
                continue
            pairs.append((score_relevance(profile, cluster, self.rules), cluster))
        chosen = select_top([score for score, _cluster in pairs], limit, self.rules.min_relevance)
        by_cluster = {cluster.cluster_id: cluster for _score, cluster in pairs}
        score_by = {score.cluster_id: score for score, _cluster in pairs}
        if not chosen:
            return []
        workers = max(1, min(self.settings.concurrency, len(chosen)))

        def job(score: RelevanceScore) -> dict[str, Any]:
            return self.run_cluster(profile, by_cluster[score.cluster_id], score, role or "ceo")

        if workers == 1:
            insights = [job(score) for score in chosen]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                insights = list(pool.map(job, chosen))
        insights.sort(key=lambda item: item["priority_score"], reverse=True)
        return insights

    def run_cluster(self, profile: Profile, cluster: NewsCluster, score: RelevanceScore, role: str) -> dict[str, Any]:
        trace_id = _new_id("tr")
        insight_id = _new_id("ins")
        budget = _budget(self.settings)
        ctx = self._context(profile.client_id, role, trace_id, budget)
        sources: dict[str, dict[str, Any]] = {}
        fragments: list[dict[str, Any]] = []
        ctx.phase = "prime"
        self._prime(ctx, cluster, sources)
        visible = _visible_features(profile, role)
        matched = [reason.feature_id for reason in score.reasons if reason.feature_id in {item["feature_id"] for item in visible}]
        ctx.phase = "plan"
        plan, fallback = self._plan(ctx, cluster, visible)
        ctx.phase = "act"
        self._act(ctx, plan, sources, fragments)
        draft_context = self._draft_context(profile, cluster, visible, matched, fragments, trace_id, sources)
        ctx.phase = "analyze"
        draft = self._analyze(ctx, cluster, visible, fragments, draft_context, critique=None)
        drafts = [draft]
        verifications = []
        bank_searched_again = False
        for iteration in range(self.settings.max_iters + 1):
            issues = check_insight(
                draft,
                allowed_sources={key: value["text"] for key, value in sources.items()},
                feature_values={item["feature_id"]: item["value"] for item in visible},
                bank_fragments=fragments,
            )
            critic_decision = None
            critic_issues: list[dict[str, str]] = []
            if not any(item["code"] in {"schema", "source_id", "number", "feature", "bank_view"} for item in issues):
                critic_decision, critic_issues = self._critic(ctx, draft, bank_searched_again)
            action = next_action(issues, critic_decision, iteration, self.settings.max_iters)
            verifications.append(
                {"iteration": iteration, "issues": issues + critic_issues, "decision": action, "critic": critic_decision}
            )
            self._mark(ctx, "verify", {"decision": action, "issues": issues + critic_issues})
            if action == "ok":
                break
            if action == "give_up_low_confidence" or iteration >= self.settings.max_iters:
                draft = _give_up(draft, draft_context, issues + critic_issues)
                drafts.append(draft)
                break
            try:
                if action == "retry_search":
                    ctx.phase = "revise"
                    self._search_bank(ctx, cluster.title, sources, fragments)
                    bank_searched_again = True
                    draft_context["bank_fragments"] = fragments
                ctx.phase = "analyze"
                draft_context["critique"] = issues + critic_issues
                draft = self._analyze(ctx, cluster, visible, fragments, draft_context, critique=issues + critic_issues)
                drafts.append(draft)
            except BudgetExceeded:
                draft = _give_up(draft, draft_context, [{"code": "budget", "detail": "лимит шагов или токенов"}])
                drafts.append(draft)
                verifications.append({"iteration": iteration, "issues": [{"code": "budget", "detail": "лимит"}], "decision": "give_up_low_confidence"})
                break
        draft["priority_score"] = priority_score(score.score, draft["impact"], draft["urgency"], draft["confidence"])
        draft["insight_id"] = insight_id
        draft["trace_id"] = trace_id
        if schema_errors(draft, "insight.schema.json"):
            draft = _give_up(grounded_draft(draft_context), draft_context, [{"code": "schema", "detail": "черновик не сошёлся со схемой"}])
            draft["priority_score"] = priority_score(score.score, draft["impact"], draft["urgency"], "low")
            draft["insight_id"] = insight_id
            draft["trace_id"] = trace_id
            draft["generated_at"] = _now()
        self.traces.save_insight(draft)
        self.traces.save_trace(
            _trace(
                trace_id=trace_id,
                run_kind="insight",
                client_id=profile.client_id,
                cluster_id=cluster.cluster_id,
                insight_id=insight_id,
                plan=plan,
                plan_fallback=fallback,
                steps=ctx.steps,
                drafts=drafts,
                verifications=verifications,
                features=draft.get("client_features_used") or [],
                sources=draft.get("sources") or [],
                budget=budget,
            )
        )
        return draft

    def chat(self, insight_id: str, question: str, role: str | None = None) -> dict[str, Any]:
        insight = self.traces.get_insight(insight_id)
        if insight is None:
            raise InsightNotFound(insight_id)
        trace_id = _new_id("tr")
        budget = _budget(self.settings)
        ctx = self._context(insight["client_id"], role or "ceo", trace_id, budget)
        sources = {item["source_id"]: {**item, "text": item.get("title") or ""} for item in insight.get("sources") or []}
        cluster = self.news.get(insight["cluster_id"])
        fragments: list[dict[str, Any]] = []
        ctx.phase = "prime"
        profile_result = self.tools.call("get_client_profile", {}, ctx)
        sources[profile_result["source_id"]] = {
            "source_id": profile_result["source_id"],
            "kind": "client_profile",
            "title": f"Профиль {ctx.session.client_id}",
            "url": None,
            "published_at": None,
            "text": profile_result.get("source_text") or "",
        }
        if cluster is not None:
            self.tools.call("get_news_cluster", {"cluster_id": cluster.cluster_id}, ctx)
            _absorb_cluster(cluster, sources)
        ctx.phase = "act"
        self._search_bank(ctx, question or insight.get("headline") or "", sources, fragments)
        for item in insight.get("bank_view") or []:
            if item.get("fragment_id") and not any(hit.get("fragment_id") == item["fragment_id"] for hit in fragments):
                fragments.append(item)
                sources[f"bank:{item['doc_id']}:{item['fragment_id']}"] = {
                    "source_id": f"bank:{item['doc_id']}:{item['fragment_id']}",
                    "kind": "bank_analytics",
                    "title": item["doc_title"],
                    "url": None,
                    "published_at": None,
                    "text": item["text"],
                }
        context = {
            "insight": insight,
            "insight_id": insight_id,
            "question": question,
            "sources": list(sources.values()),
            "bank_fragments": fragments,
            "trace_id": trace_id,
        }
        ctx.phase = "analyze"
        answer = self._chat_answer(ctx, question, insight, context)
        issues = check_chat(
            answer,
            allowed_sources={key: value.get("text") or "" for key, value in sources.items()},
            bank_fragments=fragments,
        )
        action = "ok" if not issues else "rewrite"
        if issues:
            try:
                context["critique"] = issues
                answer = self._chat_answer(ctx, question, insight, context)
                issues = check_chat(
                    answer,
                    allowed_sources={key: value.get("text") or "" for key, value in sources.items()},
                    bank_fragments=fragments,
                )
                action = "ok" if not issues else "give_up_low_confidence"
            except BudgetExceeded:
                action = "give_up_low_confidence"
        if action != "ok":
            answer = grounded_chat(context)
            answer["confidence"] = "low"
            answer["gaps"] = list(answer.get("gaps") or []) + ["проверка не подтвердила ответ, уверенность снижена"]
            answer["trace_id"] = trace_id
        self._mark(ctx, "verify", {"decision": action, "issues": issues})
        self.traces.save_trace(
            _trace(
                trace_id=trace_id,
                run_kind="chat",
                client_id=insight["client_id"],
                cluster_id=insight.get("cluster_id"),
                insight_id=insight_id,
                plan={"question": question},
                plan_fallback=False,
                steps=ctx.steps,
                drafts=[answer],
                verifications=[{"iteration": 0, "issues": issues, "decision": action}],
                features=insight.get("client_features_used") or [],
                sources=answer.get("sources") or [],
                budget=budget,
            )
        )
        return answer

    def _context(self, client_id: str, role: str, trace_id: str, budget: Budget) -> ToolContext:
        return ToolContext(
            session=Session(client_id=client_id, role=role, trace_id=trace_id),
            directory=self.directory,
            news=self.news,
            bank=self.bank,
            rules=self.rules,
            budget=budget,
        )

    def _prime(self, ctx: ToolContext, cluster: NewsCluster, sources: dict[str, dict[str, Any]]) -> None:
        profile_result = self.tools.call("get_client_profile", {}, ctx)
        sources[profile_result["source_id"]] = {
            "source_id": profile_result["source_id"],
            "kind": "client_profile",
            "title": f"Профиль {ctx.session.client_id}",
            "url": None,
            "published_at": None,
            "text": profile_result.get("source_text") or "",
        }
        self.tools.call("get_client_graph", {"depth": 1}, ctx)
        self.tools.call("get_news_cluster", {"cluster_id": cluster.cluster_id}, ctx)
        _absorb_cluster(cluster, sources)

    def _plan(self, ctx: ToolContext, cluster: NewsCluster, features: list[dict]) -> tuple[dict, bool]:
        indicator = guess_indicator(cluster)
        fallback_context = {"title": cluster.title, "cluster_id": cluster.cluster_id, "indicator": indicator}
        try:
            parsed, result, _error = self.llm.complete_json(
                kind="plan",
                role="reasoning",
                messages=plan_messages(cluster, features, self.tools),
                budget=ctx.budget,
                mock_context=fallback_context,
            )
        except BudgetExceeded:
            parsed, result = default_fallback_result(fallback_context)
        plan, fallback = coerce_plan(parsed, self.tools, fallback_context)
        self._mark(ctx, "plan", plan, model=result.model, tokens=result.prompt_tokens + result.completion_tokens)
        plan["fallback"] = fallback
        return plan, fallback

    def _act(self, ctx: ToolContext, plan: dict, sources: dict[str, dict[str, Any]], fragments: list[dict[str, Any]]) -> None:
        for step in plan.get("steps") or []:
            try:
                result = self.tools.call(step["tool"], step.get("args") or {}, ctx)
            except (ToolDenied, BudgetExceeded):
                break
            self._absorb_tool(step["tool"], result, sources, fragments)

    def _search_bank(self, ctx: ToolContext, query: str, sources: dict[str, dict[str, Any]], fragments: list[dict[str, Any]]) -> None:
        if not query.strip():
            return
        try:
            result = self.tools.call("search_bank_analytics", {"query": query, "top_k": 3}, ctx)
        except (ToolDenied, BudgetExceeded):
            return
        self._absorb_tool("search_bank_analytics", result, sources, fragments)

    def _absorb_tool(self, name: str, result: dict, sources: dict[str, dict[str, Any]], fragments: list[dict[str, Any]]) -> None:
        if name == "search_bank_analytics":
            for hit in result.get("hits") or []:
                if any(item.get("fragment_id") == hit.get("fragment_id") for item in fragments):
                    continue
                fragments.append(hit)
                sources[hit["source_id"]] = {
                    "source_id": hit["source_id"],
                    "kind": "bank_analytics",
                    "title": hit["doc_title"],
                    "url": None,
                    "published_at": hit.get("published_at"),
                    "text": hit["text"],
                }
        elif name == "get_macro_indicator":
            for match in result.get("matches") or []:
                source_id = match.get("source_id")
                if source_id and source_id not in sources:
                    sources[source_id] = {
                        "source_id": source_id,
                        "kind": match.get("kind") or "bank_analytics",
                        "title": match.get("title") or match.get("name") or "Макропоказатель",
                        "url": None,
                        "published_at": match.get("published_at"),
                        "text": match.get("text") or "",
                    }
        elif name == "get_news_cluster" and result.get("cluster_id"):
            cluster = self.news.get(result["cluster_id"])
            if cluster is not None:
                _absorb_cluster(cluster, sources)

    def _draft_context(self, profile, cluster, features, matched, fragments, trace_id, sources) -> dict[str, Any]:
        article_url = cluster.articles[0].url if cluster.articles else None
        return {
            "client_id": profile.client_id,
            "cluster_id": cluster.cluster_id,
            "title": cluster.title,
            "summary": cluster.summary or cluster.title,
            "published_at": cluster.published_at,
            "url": article_url,
            "features": features,
            "matched_feature_ids": matched,
            "bank_fragments": fragments,
            "trace_id": trace_id,
            "sources": list(sources.values()),
        }

    def _analyze(self, ctx, cluster, features, fragments, draft_context, critique) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": load_prompt("analyze")},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "features": features,
                        "bank_fragments": fragments,
                        "cluster": wrap_untrusted("news", cluster.summary or cluster.title),
                        "critique": critique,
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            parsed, result, _error = self.llm.complete_json(
                kind="analyze",
                role="reasoning",
                messages=messages,
                budget=ctx.budget,
                mock_context=draft_context,
            )
            self._mark(ctx, "analyze", {"headline": (parsed or {}).get("headline")}, model=result.model, tokens=result.prompt_tokens + result.completion_tokens)
        except BudgetExceeded:
            parsed = None
        if not parsed:
            parsed = grounded_draft(draft_context)
            parsed["confidence"] = "low"
            parsed["gaps"] = list(parsed.get("gaps") or []) + ["модель не вернула валидный JSON"]
        parsed["client_id"] = ctx.session.client_id
        parsed["cluster_id"] = cluster.cluster_id
        parsed["trace_id"] = ctx.session.trace_id
        parsed["generated_at"] = _now()
        _filter_hints(parsed, ctx.session.role)
        return parsed

    def _critic(self, ctx: ToolContext, draft: dict[str, Any], bank_searched_again: bool) -> tuple[str | None, list[dict[str, str]]]:
        messages = [
            {"role": "system", "content": load_prompt("critic")},
            {"role": "user", "content": wrap_untrusted("draft", json.dumps(draft, ensure_ascii=False))},
        ]
        try:
            parsed, result, _error = self.llm.complete_json(
                kind="critic",
                role="light",
                messages=messages,
                budget=ctx.budget,
                mock_context={"draft": draft, "bank_searched_again": bank_searched_again},
            )
        except BudgetExceeded:
            return "give_up_low_confidence", [{"code": "budget", "detail": "лимит на шаге проверки"}]
        self._mark(
            ctx,
            "critic",
            {"decision": (parsed or {}).get("decision")},
            model=result.model,
            tokens=result.prompt_tokens + result.completion_tokens,
        )
        if not parsed:
            return "rewrite", [{"code": "critic", "detail": "критик не вернул JSON"}]
        issues = [item for item in parsed.get("issues") or [] if isinstance(item, dict)]
        normalized = [{"code": str(item.get("code") or "critic"), "detail": str(item.get("detail") or "")} for item in issues]
        return str(parsed.get("decision") or "rewrite"), normalized

    def _chat_answer(self, ctx, question, insight, context) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": load_prompt("chat")},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "insight": wrap_untrusted("insight", insight.get("headline") or "")},
                    ensure_ascii=False,
                ),
            },
        ]
        try:
            parsed, result, _error = self.llm.complete_json(
                kind="chat",
                role="default",
                messages=messages,
                budget=ctx.budget,
                mock_context=context,
            )
            self._mark(ctx, "chat", {"ok": bool(parsed)}, model=result.model, tokens=result.prompt_tokens + result.completion_tokens)
        except BudgetExceeded:
            parsed = None
        if not parsed:
            parsed = grounded_chat(context)
            parsed["confidence"] = "low"
        parsed["insight_id"] = insight["insight_id"]
        parsed["question"] = question
        parsed["trace_id"] = ctx.session.trace_id
        parsed["sources"] = [_public_source(item) for item in (parsed.get("sources") or context.get("sources") or [])]
        return parsed

    def _mark(self, ctx: ToolContext, name: str, payload: dict, model: str | None = None, tokens: int = 0) -> None:
        ctx.steps.append(
            {
                "kind": "verify" if name == "verify" else "llm",
                "name": name,
                "phase": ctx.phase,
                "args": {"name": name},
                "result_preview": json.dumps(payload, ensure_ascii=False)[:400],
                "started_at": _now(),
                "duration_ms": 0,
                "tokens": tokens,
                "model": model,
                "error": False,
            }
        )


def _public_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": item.get("source_id"),
        "kind": item.get("kind"),
        "title": item.get("title") or "Источник",
        "url": item.get("url"),
        "published_at": item.get("published_at"),
    }


def _visible_features(profile: Profile, role: str) -> list[dict[str, Any]]:
    visible = []
    for feature in profile.for_role(role):
        payload = feature.public()
        payload["active"] = feature.active
        visible.append(payload)
    return visible


def _filter_hints(draft: dict[str, Any], role: str) -> None:
    allowed = _HINTS.get(role, _HINTS["ceo"])
    for action in draft.get("actions") or []:
        hint = action.get("product_hint")
        if hint not in allowed:
            action["product_hint"] = None


def _absorb_cluster(cluster: NewsCluster, sources: dict[str, dict[str, Any]]) -> None:
    source_id = f"news:{cluster.cluster_id}"
    text = "\n".join(part for part in [cluster.title, cluster.summary, *(article.text for article in cluster.articles)] if part)
    sources[source_id] = {
        "source_id": source_id,
        "kind": "news",
        "title": cluster.title,
        "url": cluster.articles[0].url if cluster.articles else None,
        "published_at": cluster.published_at,
        "text": text,
    }
    for index, article in enumerate(cluster.articles):
        article_id = f"news:{cluster.cluster_id}:{index}"
        sources[article_id] = {
            "source_id": article_id,
            "kind": "news",
            "title": article.title or cluster.title,
            "url": article.url or None,
            "published_at": article.published_at,
            "text": article.text,
        }


def _give_up(draft: dict[str, Any], context: dict[str, Any], issues: list[dict]) -> dict[str, Any]:
    if schema_errors(draft, "insight.schema.json"):
        draft = grounded_draft(context)
    draft["confidence"] = "low"
    gaps = list(draft.get("gaps") or [])
    note = "Данных не хватило для уверенного вывода: " + "; ".join(item.get("detail") or item.get("code", "") for item in issues[:3])
    if note not in gaps:
        gaps.append(note)
    draft["gaps"] = gaps
    draft["generated_at"] = draft.get("generated_at") or _now()
    return draft


def _trace(
    *,
    trace_id: str,
    run_kind: str,
    client_id: str,
    cluster_id: str | None,
    insight_id: str,
    plan: dict,
    plan_fallback: bool,
    steps: list,
    drafts: list,
    verifications: list,
    features: list,
    sources: list,
    budget: Budget,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "run_kind": run_kind,
        "client_id": client_id,
        "cluster_id": cluster_id,
        "insight_id": insight_id,
        "prompt_version": PROMPT_VERSION,
        "plan": plan,
        "plan_fallback": plan_fallback,
        "steps": steps,
        "drafts": drafts,
        "verifications": verifications,
        "features_used": features,
        "sources_used": sources,
        "usage": {
            "llm_calls": budget.llm_calls,
            "prompt_tokens": budget.prompt_tokens,
            "completion_tokens": budget.completion_tokens,
            "total_tokens": budget.tokens,
        },
        "started_at": _now(),
        "finished_at": _now(),
    }


def _budget(settings: Settings) -> Budget:
    return Budget(settings.max_steps, settings.max_tokens, settings.max_seconds, settings.max_llm_calls)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_fallback_result(context: dict[str, Any]):
    from agent.llm_client import LLMResult, default_plan

    plan = default_plan(context)
    return plan, LLMResult(json.dumps(plan, ensure_ascii=False), "fallback", 0, 0, False, "plan")
