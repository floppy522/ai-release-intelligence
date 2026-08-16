import { afterEach, expect, it, vi } from "vitest";

import { getCsrfBootstrap } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("loads an authenticated same-origin CSRF bootstrap without caching", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ csrf_token: "server-issued-token" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(getCsrfBootstrap()).resolves.toEqual({
    csrf_token: "server-issued-token",
  });
  expect(fetchMock).toHaveBeenCalledWith("/api/auth/csrf", {
    credentials: "same-origin",
    cache: "no-store",
  });
});

it.each([
  new Response("unauthorized", { status: 401 }),
  new Response(JSON.stringify({ csrf_token: "" }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }),
])("rejects failed or tokenless CSRF bootstrap responses", async (response) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

  await expect(getCsrfBootstrap()).rejects.toThrow();
});
