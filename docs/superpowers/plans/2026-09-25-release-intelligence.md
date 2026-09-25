# Release intelligence implementation

1. Add project-scoped AppConnection and requester-private PageReview records (0147 after 0143).
2. Read existing instance AI credentials through a bounded provider adapter; arbitrary configured model, explicit compatible base URL, no simulated output.
3. Capture only selected public URLs on the configured origin with pinned public DNS, checked redirects, byte/time limits, and evidence hashes. HTTP capture is never browser verification.
4. Queue capture/review jobs, snapshot selected authorized docs, persist status/evidence/result, and give readable failure states.
5. Provide connection configuration, doc selection, URL paths, job progress, results and source evidence in a mountable PageReviewPanel.
6. Test permissions, SSRF boundaries, provider failures and capture/job behavior with isolated database and mocked external network/model only. Parent owns integration/browser validation.

Browser-rendered/authenticated review remains an explicitly unavailable capture mode until a separately isolated worker and app-specific credentials are configured. The UI must say HTTP evidence only and insufficient evidence when content is missing.
