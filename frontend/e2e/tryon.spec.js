// P2 flow: photos -> garment screenshot (auto-classified + segmented) -> try-on -> side-by-side gallery.
import { expect, test } from '@playwright/test'

const fx = (n) => new URL(`./fixtures/${n}.jpg`, import.meta.url).pathname.replace(/^\/(\w:)/, '$1')

test('2D try-on from screenshot', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New here? Create an account' }).click()
  await page.getByLabel('Email').fill(`tryon-${Date.now()}@example.com`)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('Height (cm)').fill('165')
  await page.getByRole('button', { name: 'Continue' }).click()

  await page.getByTestId('upload-front').setInputFiles(fx('front'))
  await expect(page.getByRole('heading', { name: 'Side photo' })).toBeVisible()
  await page.getByTestId('upload-side').setInputFiles(fx('side'))
  await expect(page.getByAltText('Side photo')).toBeVisible()

  // Garment: auto-detected category, background removed.
  await page.getByTestId('upload-garment').setInputFiles(fx('garment'))
  const garment = page.getByTestId('garment')
  await expect(garment.getByText('Processing…')).toBeVisible()
  await expect(garment.getByLabel('Category')).toHaveValue('top', { timeout: 15_000 })

  await garment.getByRole('button', { name: 'Try it on' }).click()
  const result = page.getByTestId('tryon-result')
  await expect(result.getByTestId('tryon-status')).toHaveText('Generating…')
  await expect(result.getByTestId('tryon-status')).toHaveText('Done', { timeout: 15_000 })

  // Side-by-side: original + try-on for the supported view, honest note for the rest.
  for (const alt of ['Original front', 'Try-on front']) {
    await expect.poll(() => result.getByAltText(alt).evaluate((img) => img.naturalWidth)).toBeGreaterThan(0)
  }
  await expect(result).toContainText('side view: not supported by the current try-on model')

  // Persisted across reload.
  await page.reload()
  await expect(page.getByTestId('tryon-status')).toHaveText('Done')

  // Changing category re-processes the garment.
  await page.getByTestId('garment').getByLabel('Category').selectOption('outerwear')
  await expect(page.getByTestId('garment').getByLabel('Category')).toHaveValue('outerwear', { timeout: 15_000 })
  await expect(page.getByTestId('garment').getByRole('button', { name: 'Try it on' })).toBeVisible()
})
