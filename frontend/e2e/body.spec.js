// P3 flow: photos -> 3D body + measurements -> garment -> "no size chart" -> enter chart -> size recommendation.
import { expect, test } from '@playwright/test'

const fx = (n) => new URL(`./fixtures/${n}.jpg`, import.meta.url).pathname.replace(/^\/(\w:)/, '$1')

test('3D body, measurements and size advice', async ({ page }) => {
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))

  await page.goto('/')
  await page.getByRole('button', { name: 'New here? Create an account' }).click()
  await page.getByLabel('Email').fill(`body-${Date.now()}@example.com`)
  await page.getByLabel('Password').fill('correct-horse-battery')
  await page.getByRole('button', { name: 'Create account' }).click()
  await page.getByLabel('Height (cm)').fill('170')
  await page.getByRole('button', { name: 'Continue' }).click()

  // Fitting needs front + side.
  await page.getByRole('button', { name: 'Build my 3D body' }).click()
  await expect(page.getByRole('alert')).toContainText('Capture your front and side photo first')

  await page.getByTestId('upload-front').setInputFiles(fx('front'))
  await expect(page.getByRole('heading', { name: 'Side photo' })).toBeVisible()
  await page.getByTestId('upload-side').setInputFiles(fx('side'))
  await expect(page.getByRole('heading', { name: 'Back photo' })).toBeVisible()
  await page.getByTestId('upload-back').setInputFiles(fx('back'))
  await expect(page.getByTestId('capture-done')).toBeVisible()

  const mesh = page.waitForResponse((r) => r.url().includes('/mesh'))
  await page.getByRole('button', { name: 'Build my 3D body' }).click()
  const table = page.getByTestId('measurements')
  await expect(table).toBeVisible({ timeout: 15_000 })
  for (const label of ['Chest', 'Waist', 'Hip', 'Inseam', 'Shoulder width']) {
    await expect(table.getByRole('row', { name: new RegExp(label) })).toContainText(/\d+\.\d cm.*± [\d.]+ cm/)
  }
  const meshRes = await mesh
  expect(meshRes.status()).toBe(200)
  expect(meshRes.headers()['content-type']).toBe('model/gltf-binary')
  await expect(page.getByTestId('body-viewer').locator('canvas')).toBeVisible()
  if (process.env.E2E_SHOTS) {
    await page.waitForTimeout(1500) // let Bounds finish fitting the camera
    await page.getByTestId('body-viewer').screenshot({ path: `${process.env.E2E_SHOTS}/body-viewer.png` })
    await page.screenshot({ path: `${process.env.E2E_SHOTS}/body-page.png`, fullPage: true })
  }

  // Garment without a size chart -> honest message, then manual chart -> recommendation.
  await page.getByLabel('Category (optional)').selectOption('dress')
  await page.getByTestId('upload-garment').setInputFiles(fx('garment'))
  const garment = page.getByTestId('garment')
  await expect(garment.getByRole('button', { name: 'Try it on' })).toBeVisible({ timeout: 15_000 })
  await garment.getByRole('button', { name: 'Size advice' }).click()
  await expect(garment).toContainText('No size chart found for this garment.')

  await garment.getByRole('button', { name: 'Enter the size chart' }).click()
  const rows = [['S', '60-84', '50-68', '60-86'], ['M', '85-100', '69-84', '87-102'], ['L', '101-130', '85-110', '103-130']]
  for (const [i, [label, chest, waist, hip]] of rows.entries()) {
    await garment.getByLabel(`Size ${i + 1} label`).fill(label)
    await garment.getByLabel(`Size ${i + 1} chest`).fill(chest)
    await garment.getByLabel(`Size ${i + 1} waist`).fill(waist)
    await garment.getByLabel(`Size ${i + 1} hip`).fill(hip)
  }
  await garment.getByRole('button', { name: 'Save size chart' }).click()
  await garment.getByRole('button', { name: 'Size advice' }).click()
  await expect(garment.getByTestId('recommended-size')).toHaveText(/^Size [SML]$/)
  await expect(garment.getByTestId('size-advice').locator('li')).toHaveCount(3) // chest, waist, hip notes for a dress

  expect(errors).toEqual([])
})
