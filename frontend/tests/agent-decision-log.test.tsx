import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import AgentDecisionLog from "../components/AgentDecisionLog";
import type { RankDecision } from "../lib/aegisApi";

const domains = [
  { id: 1, domain: "urgent.example.com" },
  { id: 2, domain: "healthy.example.com" },
  { id: 3, domain: "review.example.com" },
];

const decisions: RankDecision[] = [
  {
    domain_id: 1,
    criticality_score: 94,
    decision: "auto_renew",
    reason: "Domain expiry is imminent and mandate coverage appears sufficient.",
  },
  {
    domain_id: 3,
    criticality_score: 61,
    decision: "flag_for_review",
    reason: "Certificate timing is ambiguous; keep this hostname under human review.",
  },
  {
    domain_id: 2,
    criticality_score: 12,
    decision: "ignore",
    reason: "Expiry horizons are healthy and no confirmed DNS takeover risk is present.",
  },
];

test("renders auto_renew, flag_for_review, and ignore distinctly with full reasons", () => {
  const markup = renderToStaticMarkup(
    <AgentDecisionLog domains={domains} initialDecisions={decisions} />,
  );
  assert.match(markup, /data-state="populated"/);
  assert.match(markup, /data-decision="auto_renew"/);
  assert.match(markup, /data-decision="flag_for_review"/);
  assert.match(markup, /data-decision="ignore"/);
  assert.match(markup, /Auto renew \(recommendation\)/);
  assert.match(markup, /Flag for review/);
  assert.match(markup, />Ignore</);
  assert.match(markup, /94\/100/);
  assert.match(
    markup,
    /Domain expiry is imminent and mandate coverage appears sufficient\./,
  );
  assert.match(
    markup,
    /Certificate timing is ambiguous; keep this hostname under human review\./,
  );
  assert.match(
    markup,
    /Expiry horizons are healthy and no confirmed DNS takeover risk is present\./,
  );
  assert.match(markup, /Not a payment/);
  assert.doesNotMatch(
    markup,
    /complet(?:es|ed) checkout|charg(?:es|ed) (?:a |the )?mandate/i,
  );
});

test("orders equal criticality_score decisions by ascending domain_id", () => {
  const tied: RankDecision[] = [
    {
      domain_id: 3,
      criticality_score: 50,
      decision: "flag_for_review",
      reason: "Third asset tied score.",
    },
    {
      domain_id: 1,
      criticality_score: 50,
      decision: "flag_for_review",
      reason: "First asset tied score.",
    },
    {
      domain_id: 2,
      criticality_score: 50,
      decision: "ignore",
      reason: "Second asset tied score.",
    },
  ];
  const markup = renderToStaticMarkup(
    <AgentDecisionLog domains={domains} initialDecisions={tied} />,
  );
  const first = markup.indexOf("urgent.example.com");
  const second = markup.indexOf("healthy.example.com");
  const third = markup.indexOf("review.example.com");
  assert.ok(first >= 0 && second >= 0 && third >= 0);
  assert.ok(first < second && second < third);
});

test("orders populated decisions by descending criticality_score", () => {
  const markup = renderToStaticMarkup(
    <AgentDecisionLog domains={domains} initialDecisions={decisions} />,
  );
  const urgent = markup.indexOf("urgent.example.com");
  const review = markup.indexOf("review.example.com");
  const healthy = markup.indexOf("healthy.example.com");
  assert.ok(urgent >= 0 && review >= 0 && healthy >= 0);
  assert.ok(urgent < review && review < healthy);
  assert.match(markup, /94\/100[\s\S]*61\/100[\s\S]*12\/100/);
});

test("renders empty state when ranking returns no recommendations", () => {
  const markup = renderToStaticMarkup(
    <AgentDecisionLog domains={domains} initialDecisions={[]} />,
  );
  assert.match(markup, /data-state="empty"/);
  assert.match(markup, /Ranking completed with no recommendations/);
});

test("marks retained decisions stale when domain id set changes", () => {
  const markup = renderToStaticMarkup(
    <AgentDecisionLog
      domains={domains}
      initialDecisions={decisions.slice(0, 2)}
      initialRankedDomainIds={[1, 3]}
    />,
  );
  assert.match(markup, /data-state="populated"/);
  assert.match(markup, /Inventory changed since this ranking/);
  assert.match(markup, /run ranking again/i);
});

test("renders loading state", () => {
  const markup = renderToStaticMarkup(
    <AgentDecisionLog domains={domains} initialLoading />,
  );
  assert.match(markup, /data-state="loading"/);
  assert.match(markup, /Ranking domains/);
});

test("renders API error state", () => {
  const markup = renderToStaticMarkup(
    <AgentDecisionLog
      domains={domains}
      initialError="Automated ranking is unavailable"
    />,
  );
  assert.match(markup, /data-state="error"/);
  assert.match(markup, /Automated ranking is unavailable/);
});

test("renders idle empty guidance when no domains are scanned", () => {
  const markup = renderToStaticMarkup(<AgentDecisionLog domains={[]} />);
  assert.match(markup, /data-state="idle"/);
  assert.match(markup, /Scan domains first/);
});
