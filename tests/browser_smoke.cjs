const { chromium } = require("playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  });
  const issues = [];
  const results = [];
  for (const viewport of [
    { width: 1440, height: 1000, name: "desktop" },
    { width: 320, height: 900, name: "mobile" },
  ]) {
    const page = await browser.newPage({ viewport });
    await page.route("**/api/content-batches?limit=1", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          data: [{
            batchId: "browser-test-batch",
            counts: { READY: 1 },
            jobs: [{
              jobId: "browser-test-job",
              sourceUrl: "https://reviews.example/best-robot-vacuums",
              status: "READY",
              articleTitle: "Best Robot Vacuums for Pet Hair",
              keyword: "Best Robot Vacuums for Pet Hair",
              contentType: "ROUNDUP",
              confidence: 92,
              revenuePotential: "HIGH",
              isApproved: false,
              products: [{
                asin: "B0ABC12345",
                name: "CleanBot X1",
                validationStatus: "VERIFIED",
                availability: "IN_STOCK",
                isIncluded: true,
                duplicateAcrossBatch: true,
                batchOccurrenceCount: 2,
              }],
            }],
          }],
        }),
      })
    );
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        issues.push(`${viewport.name}:${message.type()}:${message.text()}`);
      }
    });
    page.on("pageerror", (error) => {
      issues.push(`${viewport.name}:pageerror:${error.message}`);
    });
    page.on("response", (response) => {
      if (response.status() >= 400) {
        issues.push(`${viewport.name}:http${response.status()}:${response.url()}`);
      }
    });
    const response = await page.goto("http://127.0.0.1:7504/", {
      waitUntil: "networkidle",
    });
    const controls = await page.locator(
      "#output_root,#product_order,#content_mode,#music_mode,#hands_on_notes,#gemini_tts_model,#gemini_voice_style"
    ).count();
    await page.getByRole("button", { name: "Review", exact: true }).click();
    const reviewControls = await page.locator(
      ".review-keyword,.review-content-type,.review-product,.review-product-rank,.review-product-asin"
    ).count();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1
    );
    const overflowElements = await page.evaluate(() =>
      [...document.querySelectorAll("body *")]
        .filter((element) => element.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 8)
        .map((element) => ({
          tag: element.tagName,
          id: element.id,
          className: String(element.className).slice(0, 120),
          right: Math.round(element.getBoundingClientRect().right),
        }))
    );
    await page.screenshot({
      path: `tests/browser-${viewport.name}.png`,
      fullPage: true,
    });
    results.push({
      viewport: viewport.name,
      status: response.status(),
      title: await page.title(),
      controls,
      reviewControls,
      reviewTableRows: await page.locator("#contentReviewBody > tr").count(),
      overflow,
      overflowElements,
      hasCsrfMeta: (await page.locator('meta[name="csrf-token"]').count()) === 1,
    });
    await page.close();
  }
  for (const target of [
    {
      path: "/settings",
      name: "settings",
      expected: "#tts_service,#gemini_tts_model,#gemini_tts_voice,#gemini_voice_style,#gemini_voice_pace",
    },
    { path: "/upload", name: "upload", expected: "#videoList" },
  ]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    page.on("console", (message) => {
      if (["error", "warning"].includes(message.type())) {
        issues.push(`${target.name}:${message.type()}:${message.text()}`);
      }
    });
    page.on("pageerror", (error) => {
      issues.push(`${target.name}:pageerror:${error.message}`);
    });
    if (target.name === "upload") {
      await page.route("**/check_auth", (route) =>
        route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({ authenticated: true, channel_name: "Test Channel" }),
        })
      );
      await page.route("**/get_playlists", (route) =>
        route.fulfill({ contentType: "application/json", body: "[]" })
      );
      await page.route("**/list_videos", (route) =>
        route.fulfill({ contentType: "application/json", body: "[]" })
      );
      await page.route("**/bg_list", (route) =>
        route.fulfill({ contentType: "application/json", body: "[]" })
      );
    }
    const response = await page.goto(`http://127.0.0.1:7504${target.path}`, {
      waitUntil: "networkidle",
    });
    results.push({
      viewport: target.name,
      status: response.status(),
      controls: await page.locator(target.expected).count(),
      overflow: await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 1
      ),
      hasCsrfMeta: (await page.locator('meta[name="csrf-token"]').count()) === 1,
    });
    await page.screenshot({
      path: `tests/browser-${target.name}.png`,
      fullPage: true,
    });
    await page.close();
  }
  await browser.close();
  console.log(JSON.stringify({ results, issues }, null, 2));
  const failures = [];
  for (const result of results) {
    if (result.status !== 200) failures.push(`${result.viewport}: HTTP ${result.status}`);
    if (result.overflow) failures.push(`${result.viewport}: page overflow`);
    if (!result.hasCsrfMeta) failures.push(`${result.viewport}: missing CSRF meta`);
    if (["desktop", "mobile"].includes(result.viewport)) {
      if (result.controls !== 7) failures.push(`${result.viewport}: missing editor controls`);
      if (result.reviewControls !== 5) failures.push(`${result.viewport}: missing review controls`);
      if (result.reviewTableRows !== 2) failures.push(`${result.viewport}: review table not rendered`);
    }
    if (result.viewport === "settings" && result.controls !== 5) {
      failures.push("settings: missing Gemini controls");
    }
  }
  failures.push(...issues);
  if (failures.length) {
    throw new Error(`Browser smoke failures:\n${failures.join("\n")}`);
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
