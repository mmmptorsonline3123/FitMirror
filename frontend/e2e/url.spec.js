// P4 flow: product link -> garment with title + size chart from the page -> size advice; robots/SSRF refusals fall back to screenshot.
import { expect, test } from '@playwright/test'

const fx = (n) => new URL(`./fixtures/${n}.jpg`, import.meta.url).pathname.replace(/^\/(\w:)/, '$1')
const SHOP = 'http://127.0.0.1:5056'
const FETCH = { timeout: 25_000 } // results of a server-side fetch: match the server's 25 s budget (slow under parallel load)

test('garment from product URL', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'New here? Create an account' }).click()
  await page.getByLabel('Email').fill(`url-${Date.now()}@example.com`)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('Height (cm)').fill('168')
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByTestId('upload-front').setInputFiles(fx('front'))
  await expect(page.getByRole('heading', { name: 'Side photo' })).toBeVisible()
  await page.getByTestId('upload-side').setInputFiles(fx('side'))
  await expect(page.getByAltText('Side photo')).toBeVisible()
  await page.getByRole('button', { name: 'Build my 3D body' }).click()
  await expect(page.getByTestId('measurements')).toBeVisible({ timeout: 15_000 })

  // Refusals: robots.txt, private address, non-standard port -> clear reason + screenshot fallback.
  const link = page.getByLabel('Product link')
  const fetchBtn = page.getByRole('button', { name: 'Fetch product' })
  await link.fill(`${SHOP}/private/product.html`)
  await fetchBtn.click()
  await expect(page.getByTestId('url-error')).toContainText("doesn't allow automated access", FETCH)
  await expect(page.getByTestId('url-error')).toContainText('upload a screenshot')
  await link.fill('http://169.254.169.254/latest/meta-data/')
  await fetchBtn.click()
  await expect(page.getByTestId('url-error')).toContainText('private or local network address', FETCH)
  await link.fill('http://127.0.0.1:8000/health')
  await fetchBtn.click()
  await expect(page.getByTestId('url-error')).toContainText('standard web ports', FETCH)
  await expect(page.getByTestId('garment')).toHaveCount(0)

  // Success: JSON-LD image (first one 404s, second used), title and size chart from the page.
  await page.getByLabel('Category (optional)').selectOption('dress')
  await link.fill(`${SHOP}/product.html`)
  await fetchBtn.click()
  const garment = page.getByTestId('garment')
  await expect(garment).toContainText('E2E Linen Dress', FETCH)
  await expect(garment).toContainText('from 127.0.0.1')
  await expect(garment).toContainText('Size chart found on the page')
  await expect(page.getByTestId('url-error')).toHaveCount(0)
  await expect(garment.getByRole('button', { name: 'Try it on' })).toBeVisible({ timeout: 15_000 })

  await garment.getByRole('button', { name: 'Size advice' }).click()
  await expect(garment.getByTestId('recommended-size')).toHaveText(/^Size [SML]$/)
  await expect(garment.getByRole('button', { name: 'Enter the size chart' })).toHaveCount(0)
})
