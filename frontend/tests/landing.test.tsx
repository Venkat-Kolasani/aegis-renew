import assert from "node:assert/strict";
import { test } from "node:test";
import { renderToStaticMarkup } from "react-dom/server";
import LandingPage from "../components/LandingPage";

test("landing page renders hero and dashboard link", () => {
  const markup = renderToStaticMarkup(<LandingPage />);
  assert.match(markup, /Renewal happens on/);
  assert.match(markup, /Open operations console/);
  assert.match(markup, /href="\/dashboard"/);
  assert.match(markup, /How it works/);
  assert.match(markup, /One pipeline, four hard gates/);
});
