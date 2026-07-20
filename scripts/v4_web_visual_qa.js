async (page) => {
  const cases = [
    { name: "light-390x844", theme: "light", width: 390, height: 844 },
    { name: "dark-768x1024", theme: "dark", width: 768, height: 1024 },
    { name: "system-1280x720", theme: "system", width: 1280, height: 720 },
    { name: "light-1440x900", theme: "light", width: 1440, height: 900 }
  ];
  const results = [];
  for (const item of cases) {
    await page.setViewportSize({ width: item.width, height: item.height });
    await page.goto(`http://127.0.0.1:8123/desktop_console.html?theme=${item.theme}`, { waitUntil: "domcontentloaded" });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    await page.waitForTimeout(350);
    const metrics = await page.evaluate(() => ({
      theme: document.documentElement.getAttribute("data-theme") || "system",
      bodyWidth: document.body.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      bodyHeight: document.body.scrollHeight,
      visibleText: (document.body.innerText || "").trim().length,
      background: getComputedStyle(document.body).backgroundColor
    }));
    const screenshot = `output/playwright/v4-desktop-console-${item.name}.png`;
    await page.screenshot({ path: screenshot, fullPage: true });
    results.push({ ...item, ...metrics, overflowX: metrics.bodyWidth > metrics.viewportWidth, screenshot });
  }
  return results;
}
