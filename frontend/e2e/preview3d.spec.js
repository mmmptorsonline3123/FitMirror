// P5 flow (feature-flagged): garment shown on the 3D avatar, labelled "Preview". Flag off -> no entry point.
import { expect, test } from '@playwright/test'

const fx = (n) => new URL(`./fixtures/${n}.jpg`, import.meta.url).pathname.replace(/^\/(\w:)/, '$1')

async function readyUserWithGarment(page, prefix) {
  await page.goto('/')
  await page.getByRole('button', { name: 'New here? Create an account' }).click()
  await page.getByLabel('Email').fill(`${prefix}-${Date.now()}@example.com`)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('Height (cm)').fill('172')
  await page.getByRole('button', { name: 'Continue' }).click()
  await page.getByTestId('upload-front').setInputFiles(fx('front'))
  await expect(page.getByRole('heading', { name: 'Side photo' })).toBeVisible()
  await page.getByTestId('upload-side').setInputFiles(fx('side'))
  await expect(page.getByAltText('Side photo')).toBeVisible()
  await page.getByRole('button', { name: 'Build my 3D body' }).click()
  await expect(page.getByTestId('measurements')).toBeVisible({ timeout: 15_000 })
  await page.getByLabel('Category (optional)').selectOption('dress')
  await page.getByTestId('upload-garment').setInputFiles(fx('garment'))
  const garment = page.getByTestId('garment')
  await expect(garment.getByRole('button', { name: 'Try it on' })).toBeVisible({ timeout: 15_000 })
  return garment
}

test('3D garment preview with flag on', async ({ page }) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  const garment = await readyUserWithGarment(page, 'p3d')

  const mesh = page.waitForResponse((r) => r.url().includes('/tryon3d/') && r.url().endsWith('/mesh'))
  await garment.getByRole('button', { name: /3D preview/ }).click()
  const label = page.getByTestId('preview-label')
  await expect(label).toContainText('Preview')
  await expect(label).toContainText('dress shown on your body')
  const res = await mesh
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toBe('model/gltf-binary')
  await expect(page.getByTestId('body-viewer').locator('canvas')).toBeVisible()
  if (process.env.E2E_SHOTS) {
    await page.waitForTimeout(1500)
    await page.getByTestId('body-viewer').screenshot({ path: `${process.env.E2E_SHOTS}/preview3d.png` })
  }

  await label.getByRole('button', { name: 'Hide' }).click()
  await expect(label).toHaveCount(0)
  expect(errors).toEqual([])
})

test('no 3D preview entry point with flag off', async ({ page }) => {
  await page.route('**/api/features', (route) => route.fulfill({ json: { garment_3d: false } }))
  const garment = await readyUserWithGarment(page, 'p3doff')
  await expect(garment.getByRole('button', { name: /3D preview/ })).toHaveCount(0)
  await expect(page.getByTestId('preview-label')).toHaveCount(0)
})
