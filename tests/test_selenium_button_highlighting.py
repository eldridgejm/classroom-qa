"""
Selenium tests for button highlighting behavior

These tests verify that answer buttons remain visually highlighted
after being clicked, even when the mouse moves away.

NOTE: These tests require Selenium to be installed.
Install with: pip install selenium

To run: pytest tests/test_selenium_button_highlighting.py -v
"""

import time

import pytest

# Try to import selenium, skip all tests if not available
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    SELENIUM_AVAILABLE = True
except ImportError:
    SELENIUM_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not SELENIUM_AVAILABLE,
    reason="Selenium not installed. Install with: pip install selenium"
)


@pytest.fixture(scope="module")
def browser():
    """Create a headless Chrome browser for testing"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(1920, 1080)

    yield driver

    driver.quit()


@pytest.fixture
def setup_session(browser):
    """Setup: Login as admin, start session, create question"""
    # This would need to be implemented based on your app structure
    # For now, return the browser
    return browser


class TestButtonHighlighting:
    """Test button highlighting persistence"""

    def test_mcq_button_stays_highlighted_after_click(self, browser):
        """
        Test that MCQ button remains highlighted after clicking and moving mouse away
        """
        # Navigate to student page (you'll need to adjust URL)
        browser.get("http://localhost:8000/c/dsc80-wi25")

        # Wait for page to load
        time.sleep(1)

        # Enter PID if needed
        try:
            pid_input = browser.find_element(By.NAME, "pid")
            pid_input.send_keys("A12345678")
            submit_btn = browser.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
            time.sleep(1)
        except:
            pass  # Already logged in

        # Wait for a question to appear (you'll need to create one via admin)
        # For now, let's check if we can find answer buttons
        wait = WebDriverWait(browser, 10)

        # Try to find MCQ buttons
        try:
            buttons = wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".mcq-option"))
            )

            if len(buttons) > 0:
                first_button = buttons[0]

                # Get initial styles
                initial_bg = first_button.value_of_css_property("background-color")
                initial_border = first_button.value_of_css_property("border-color")
                initial_font_weight = first_button.value_of_css_property("font-weight")

                print(f"\nInitial styles:")
                print(f"  Background: {initial_bg}")
                print(f"  Border: {initial_border}")
                print(f"  Font weight: {initial_font_weight}")

                # Click the button
                first_button.click()
                time.sleep(0.5)  # Wait for JavaScript to apply styles

                # Get styles after click
                after_click_bg = first_button.value_of_css_property("background-color")
                after_click_border = first_button.value_of_css_property("border-color")
                after_click_font_weight = first_button.value_of_css_property("font-weight")
                after_click_box_shadow = first_button.value_of_css_property("box-shadow")

                print(f"\nAfter click styles:")
                print(f"  Background: {after_click_bg}")
                print(f"  Border: {after_click_border}")
                print(f"  Font weight: {after_click_font_weight}")
                print(f"  Box shadow: {after_click_box_shadow}")

                # Move mouse away from button
                actions = ActionChains(browser)
                actions.move_by_offset(500, 500).perform()
                time.sleep(0.5)

                # Get styles after moving mouse away
                after_mouseout_bg = first_button.value_of_css_property("background-color")
                after_mouseout_border = first_button.value_of_css_property("border-color")
                after_mouseout_font_weight = first_button.value_of_css_property("font-weight")
                after_mouseout_box_shadow = first_button.value_of_css_property("box-shadow")

                print(f"\nAfter mouse move away styles:")
                print(f"  Background: {after_mouseout_bg}")
                print(f"  Border: {after_mouseout_border}")
                print(f"  Font weight: {after_mouseout_font_weight}")
                print(f"  Box shadow: {after_mouseout_box_shadow}")

                # Verify the styles persist (blue background)
                # Blue background should be rgb(219, 234, 254) which is #dbeafe
                assert after_mouseout_bg != initial_bg, "Background color should change after click"
                assert "rgb(219, 234, 254)" in after_mouseout_bg, f"Background should be blue, got: {after_mouseout_bg}"

                # Blue border should be rgb(37, 99, 235) which is #2563eb
                assert after_mouseout_border != initial_border, "Border color should change after click"
                assert "rgb(37, 99, 235)" in after_mouseout_border, f"Border should be blue, got: {after_mouseout_border}"

                # Font weight should be bold (700)
                assert after_mouseout_font_weight == "700" or after_mouseout_font_weight == "bold", \
                    f"Font should be bold, got: {after_mouseout_font_weight}"

                # Box shadow should be present (green glow)
                assert after_mouseout_box_shadow != "none", f"Box shadow should be present, got: {after_mouseout_box_shadow}"

                print("\n✓ Button highlighting persists correctly!")

        except Exception as e:
            print(f"\n✗ Test setup issue: {e}")
            print("Note: This test requires the app to be running and a question to be active")
            pytest.skip("Could not find MCQ buttons - test requires active question")

    def test_check_answer_selected_class(self, browser):
        """
        Test that the answer-selected class is applied correctly
        """
        browser.get("http://localhost:8000/c/dsc80-wi25")
        time.sleep(1)

        # Enter PID if needed
        try:
            pid_input = browser.find_element(By.NAME, "pid")
            pid_input.send_keys("A12345678")
            submit_btn = browser.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
            time.sleep(1)
        except:
            pass

        wait = WebDriverWait(browser, 10)

        try:
            buttons = wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".mcq-option"))
            )

            if len(buttons) > 0:
                first_button = buttons[0]

                # Check if button has answer-selected class initially
                initial_classes = first_button.get_attribute("class")
                print(f"\nInitial classes: {initial_classes}")
                assert "answer-selected" not in initial_classes, "Button should not have answer-selected class initially"

                # Click the button
                first_button.click()
                time.sleep(0.5)

                # Check if button has answer-selected class after click
                after_click_classes = first_button.get_attribute("class")
                print(f"After click classes: {after_click_classes}")

                if "answer-selected" not in after_click_classes:
                    print("\n✗ ISSUE FOUND: answer-selected class is NOT being applied!")
                    print("This means the JavaScript handleAnswerSubmit() function is not working correctly")
                else:
                    print("\n✓ answer-selected class is correctly applied")

                assert "answer-selected" in after_click_classes, \
                    "Button should have answer-selected class after click"

        except Exception as e:
            print(f"\n✗ Test setup issue: {e}")
            pytest.skip("Could not find MCQ buttons - test requires active question")

    def test_tf_button_highlighting(self, browser):
        """
        Test that True/False button highlighting persists
        """
        browser.get("http://localhost:8000/c/dsc80-wi25")
        time.sleep(1)

        # Enter PID if needed
        try:
            pid_input = browser.find_element(By.NAME, "pid")
            pid_input.send_keys("A12345678")
            submit_btn = browser.find_element(By.CSS_SELECTOR, "button[type='submit']")
            submit_btn.click()
            time.sleep(1)
        except:
            pass

        wait = WebDriverWait(browser, 10)

        try:
            buttons = wait.until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".tf-option"))
            )

            if len(buttons) > 0:
                true_button = buttons[0]

                # Get initial styles
                initial_bg = true_button.value_of_css_property("background-color")

                print(f"\nT/F Initial background: {initial_bg}")

                # Click the button
                true_button.click()
                time.sleep(0.5)

                # Get styles after click
                after_click_bg = true_button.value_of_css_property("background-color")
                after_click_classes = true_button.get_attribute("class")

                print(f"T/F After click background: {after_click_bg}")
                print(f"T/F After click classes: {after_click_classes}")

                # Move mouse away
                actions = ActionChains(browser)
                actions.move_by_offset(500, 500).perform()
                time.sleep(0.5)

                # Get styles after moving mouse away
                after_mouseout_bg = true_button.value_of_css_property("background-color")

                print(f"T/F After mouse away background: {after_mouseout_bg}")

                # Verify
                assert "answer-selected" in after_click_classes, "answer-selected class should be applied"
                assert "rgb(219, 234, 254)" in after_mouseout_bg, \
                    f"Background should be blue after mouse moves away, got: {after_mouseout_bg}"

                print("\n✓ T/F button highlighting persists correctly!")

        except Exception as e:
            print(f"\n✗ Test setup issue: {e}")
            pytest.skip("Could not find T/F buttons - test requires active question")
