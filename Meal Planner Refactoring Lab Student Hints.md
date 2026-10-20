# Meal Planner Refactoring Lab: Student Hints


## Step 1: Select and run the baseline

Goal: get the original running once and write down what happens, including what breaks.

**Where to look:** the Configuration cell and the install cell.

**Ask yourself:**

- Rename your .env file for a moment and run the config cells. Which line fails, and does the error message tell you the key is missing?
- Look at every value typed directly into the config cell. Which of these should never be inside a notebook you share or push to GitHub?
- What happens when you click Email Plan with the placeholder email settings still in place?
- Is every package in the install line actually used by the code?

**Record:** one successful run (a generated plan) as your baseline. After refactoring, valid input must give the same kind of result.

## Step 2: Analyze the code

Goal: a prioritized checklist of problems before you change a single line. There are at least 10 to find; aim for 8.

| Where to look | Category | Question to ask |
| --- | --- | --- |
| Configuration cell | Hardcoded values | Which strings are secrets? What happens if this file goes on GitHub? |
| `MealPlanGenerator.generate()` | Monolithic function | Count the separate jobs this one method does. How many did you get? |
| `generate()`, the `recipe_chain.invoke(...)` line | Missing error handling | Is the API call inside or outside the `try`? What happens with a wrong API key? |
| `generate()`, the `except` blocks | Silent failure | The error message says *what* went wrong. Does it say *where*? |
| `generate()`, the code-fence stripping | Fragile logic | What if the model writes a sentence before the JSON, or forgets the closing fence? |
| `generate()`, the two return dicts | Inconsistent output | Do the success and failure dicts have the same keys? Which key is missing, and who reads it? |
| `format_meal_plan_email()` and `format_single_recipe()` | Repeated code | Which line turns ingredients into text in both? |
| `generate_plan_streaming()` and `send_email_ui()` | Repeated code + bug | What is copy-pasted between them? And which plan does the email button actually send? |
| `__init__` and the prompts cell | Dead code | Is every parser, prompt and constant actually used somewhere? |
| `run_basic_validation()` | Hardcoded values | The numbers 5 and 7 appear here. Is there already a constant for them? |
| Pydantic models vs `run_basic_validation()` | Duplicated logic | How many places check the same rules? |
| `send_meal_plan_email()` | Mixed concerns | Building a message and sending it over the network: one job or two? |
| Test cases cell | Mixed concerns | Test data, the loop and the printing all live together. Could the test cases come from a file instead? |

End this step with the lab's checklist template filled in and your top 3 priorities. Tip: prioritize anything that leaks secrets or fails silently above anything that just looks untidy.

## Step 3: Create helper functions

Goal: every piece of logic that appears more than once lives in one small function.

**Patterns to hunt for (search the notebook for each):**

- A loop that collects ingredient names from every recipe. How many cells contain it?
- "If the textbox is empty, use a default." Where is that written out by hand?
- Removing ```` ``` ```` fences from model output. Is it done in more than one way?
- The dictionary each evaluator returns (`key`, `score`, `comment`). How many times is that shape typed?
- Turning a recipe's ingredients into one line of text. Do the email and the UI both do it?
- One error message format. Could every error in the notebook go through the same printer?

**Ask yourself:** can I test this helper with a small fake dictionary, without calling OpenAI? If not, it is doing too much.

**Done when:** a test cell calls each helper with hand-made data and prints the result.

## Step 4: Modularize the functions

Goal: each function does one job, and you can describe it in one sentence without using the word "and".

**Try the "and" test on these:**

| Function | Describe it in one sentence | How many "and"s? |
| --- | --- | --- |
| `generate()` |  |  |
| `send_meal_plan_email()` |  |  |
| `generate_plan_streaming()` |  |  |
| `send_email_ui()` |  |  |
| `create_or_get_dataset()` |  |  |
| each evaluator |  |  |

Fill in the middle column yourself. Every "and" is a candidate for a split.

**Ask yourself:**

- Can I test JSON parsing without paying for an API call? If not, parsing is stuck inside the API function.
- If the email server changes, how many functions do I have to touch?
- After the split, does `generate()` mostly call other functions in order? That is the goal: it orchestrates, it doesn't do the work.

## Step 5: Add error handling

Goal: when anything fails, the message names the function, the error type, the location, and a suggestion.

**Break it on purpose, then read what the original prints:**

| Break this | How | What does the original show? |
| --- | --- | --- |
| OpenAI key | Delete it, then try a fake one |  |
| Network | Turn off Wi-Fi, click Generate |  |
| JSON from the model | Pass `'{"recipes": [}'` to the parsing code |  |
| Meal-plan schema | Feed a plan with 6 recipes or 4 ingredients |  |
| Email | Use a wrong app password |  |
| Empty textbox | Leave Themes empty, then try `None` |  |

If the right column says "crash", "nothing" or a message with no location, you found a gap.

**Hints, not answers:**

- The OpenAI library has its own exception classes for auth, rate limits, timeouts and connection problems. Look them up; catching `Exception` treats them all the same.
- `json.JSONDecodeError` carries the position of the problem. Check its attributes.
- Pydantic's `ValidationError` can tell you exactly which field failed.
- For each `except`, decide: should this stop the program (`raise`) or return a failure the UI can show? Printing and then carrying on silently is the worst of both.
- A bare `except:` also catches things like Ctrl+C. Why is that a problem?

## Step 6: Test the refactored code

Goal: valid input behaves exactly like your Step 1 baseline, and every broken input from Step 5 now gives a clear message.

- Re-run the Step 5 table against your new code and fill in a fourth column: "What does mine show?" Screenshot each one.
- Generate one real plan and compare it with the baseline. Same structure, 7 recipes, 5 ingredients each?
- Click Generate, then Email. Is the emailed plan the same one you see on screen?

**The file-input gap:** the lab asks for `FileNotFoundError` and malformed-JSON tests, but this app takes its input from a form, not a file. Look at the `TEST_CASES` list. Is there a way to make it a real input file, so you can test a missing file, a broken file (`malformed.json`) and bad data (`invalid_products.json`-style)?

Test cells should not call OpenAI unless they are clearly marked as a real API test. Why does that matter when 30 students run the notebook?

## Steps 7–8 (optional): API wrapper and logging

**API wrapper:**

- Count how many places create a `ChatOpenAI` or call `.invoke()`. A wrapper gives all of them one door.
- Which errors are worth retrying? Would retrying a wrong API key ever help? What about a rate limit?
- If you retry immediately, what happens to a server that is already overloaded? Look up "exponential backoff".

**Logging:**

- Which events would you want to see the morning after a failed run: file loaded, validation result, API call start and end, retries, email sent?
- What is the difference between `print()` and the `logging` module when the app runs unattended?
- Check your log file: does any line contain an API key or a password?

## Final self-check

- [ ] No secrets anywhere in the notebook, the outputs or the screenshots, and .env is listed in .gitignore
- [ ] No bare `except:` left
- [ ] Every error message names the function, the error type, the location and a suggestion
- [ ] The API call itself is protected, not just the parsing after it
- [ ] No copy-pasted blocks between cells
- [ ] Unused code removed
- [ ] The emailed plan matches the plan on screen
- [ ] Screenshots: one success, invalid data, a missing file, an API error
- [ ] `lab_summary.md`: one paragraph with the path you chose, what you changed, your main challenge, what you learned
- [ ] Before/after comparison included
