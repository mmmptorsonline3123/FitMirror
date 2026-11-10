// P1 flow: register -> height -> 3 photos (one rejection) -> delete all data -> cannot sign in again.
import { expect, test } from '@playwright/test'

const fx = (n) => new URL(`./fixtures/${n}.jpg`, import.meta.url).pathname.replace(/^\/(\w:)/, '$1')

test('capture flow and delete-all', async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`
  const password = 'correct-horse-battery'

  await page.goto('/')
  await page.getByRole('button', { name: 'New here? Create an account' }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()

  await page.getByLabel('Height (cm)').fill('172')
  await page.getByRole('button', { name: 'Continue' }).click()
  await expect(page.getByText('height 172 cm')).toBeVisible()

  // Rejection with a specific reason, then a valid photo.
  await page.getByTestId('upload-front').setInputFiles(fx('blank'))
  await expect(page.getByTestId('photo-error')).toContainText('No person detected')
  await page.getByTestId('upload-front').setInputFiles(fx('front'))
  await expect(page.getByRole('heading', { name: 'Side photo' })).toBeVisible()
  await page.getByTestId('upload-side').setInputFiles(fx('side'))
  await expect(page.getByRole('heading', { name: 'Back photo' })).toBeVisible()
  await page.getByTestId('upload-back').setInputFiles(fx('back'))
  await expect(page.getByTestId('capture-done')).toBeVisible()

  // Thumbnails load through short-lived signed URLs, not public links.
  const src = await page.getByAltText('Front photo').getAttribute('src')
  expect(src).toContain('X-Amz-Signature')
  await expect.poll(() => page.getByAltText('Front photo').evaluate((img) => img.naturalWidth)).toBeGreaterThan(0)

  // Survives reload (session cookie + persisted photos).
  await page.reload()
  await expect(page.getByTestId('capture-done')).toBeVisible()

  page.on('dialog', (d) => d.accept())
  await page.getByRole('button', { name: 'Delete all my data' }).click()
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()

  // Photo object is gone from storage, and the account no longer exists.
  expect((await page.request.get(src)).status()).toBe(404)
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText('Wrong email or password')
})
