const { chromium } = require('C:/Users/admin/AppData/Roaming/npm/node_modules/n8n/node_modules/playwright-core');
const fs = require('fs');
const path = require('path');

(async () => {
  const root = process.argv[2];
  if (!root) throw new Error('Missing bundle path');
  const browser = await chromium.launch({
    headless: true,
    executablePath: 'C:/Users/admin/AppData/Local/ms-playwright/chromium-1254/chrome-win64/chrome.exe',
  });
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });

  for (const dir of fs.readdirSync(root, { withFileTypes: true }).filter((entry) => entry.isDirectory())) {
    const file = path.join(root, dir.name, 'code.html');
    if (!fs.existsSync(file)) continue;
    const page = dir.name.includes('mobile') ? mobile : desktop;
    await page.goto(`file://${file.replaceAll('\\', '/')}`, { waitUntil: 'networkidle' }).catch(() => {});
    await page.screenshot({ path: path.join(root, dir.name, 'screen.png'), fullPage: true });
  }
  await browser.close();
})();
