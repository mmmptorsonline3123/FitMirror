// Wardrobe flow: two garments -> two try-ons -> compare side by side -> remove a try-on -> remove a garment (cascades).
import { expect, test } from '@playwright/test'

const fx = (n) => new URL(`./fixtures/${n}.jpg`, import.meta.url).pathname.replace(/^\/(\w:)/, '$1')

test('wardrobe compare and remove', async ({ page }) => {
  page.on('dialog', (d) => d.accept())
  await page.goto('/')
  await page.getByRole('button', { name: 'New here? Create an account' }).click()
  await page.getByLabel('Email').fill(`wardrobe-${Date.now()}@example.com`)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('Height (cm)').fill('170')
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByTestId('upload-front').setInputFiles(fx('front'))
  await expect(page.getByRole('heading', { name: 'Side photo' })).toBeVisible()

  const card = (cat) => page.getByTestId('garment').filter({ has: page.getByAltText(`Garment (${cat})`) })
  for (const cat of ['top', 'dress']) {
    await page.getByLabel('Category (optional)').selectOption(cat)
    await page.getByTestId('upload-garment').setInputFiles(fx('garment'))
    await expect(card(cat).getByRole('button', { name: 'Try it on' })).toBeVisible({ timeout: 15_000 })
    await card(cat).getByRole('button', { name: 'Try it on' }).click()
  }
  const results = page.getByTestId('tryon-result')
  await expect(results).toHaveCount(2)
  await expect(page.getByTestId('tryon-status').filter({ hasText: 'Done' })).toHaveCount(2, { timeout: 20_000 })
  await expect(page.getByText('Your wardrobe')).toBeVisible()

  // Compare: needs 2 ticked.
  await expect(page.getByTestId('compare-hint')).toBeVisible()
  await page.getByLabel('Compare top').check()
  await expect(page.getByTestId('compare')).toHaveCount(0)
  await page.getByLabel('Compare dress').check()
  const compare = page.getByTestId('compare')
  await expect(compare).toContainText('Comparing 2 try-ons')
  await expect(compare.getByTestId('compare-item')).toHaveCount(2)
  for (const alt of ['You (original)', 'Try-on top', 'Try-on dress']) {
    await expect.poll(() => compare.getByAltText(alt).evaluate((img) => img.naturalWidth)).toBeGreaterThan(0)
  }
  if (process.env.E2E_SHOTS) await compare.screenshot({ path: `${process.env.E2E_SHOTS}/compare.png` })
  await compare.getByRole('button', { name: 'Clear' }).click()
  await expect(compare).toHaveCount(0)

  // Remove one try-on, then a garment (its try-on goes with it); survives reload.
  await results.filter({ hasText: 'dress' }).getByRole('button', { name: 'Remove' }).click()
  await expect(results).toHaveCount(1)
  await card('top').getByRole('button', { name: 'Remove garment' }).click()
  await expect(card('top')).toHaveCount(0)
  await expect(results).toHaveCount(0)
  await page.reload()
  await expect(page.getByTestId('garment')).toHaveCount(1)
  await expect(card('dress')).toHaveCount(1)
  await expect(page.getByTestId('tryon-result')).toHaveCount(0)
})
