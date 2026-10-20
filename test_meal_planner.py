import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from meal_planner import (
    MealPlanGenerator,
    build_email_message,
    extract_json_object,
    format_ingredients,
    load_test_cases,
    normalize_optional_text,
    parse_meal_plan,
    run_basic_validation,
)


def sample_meal_plan():
    recipes = []
    themes = ["Italian", "Mexican", "Japanese", "Indian", "Thai", "Greek", "French"]
    for index, theme in enumerate(themes):
        recipes.append({
            "day": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][index],
            "theme": theme,
            "dish_name": f"Dish {index + 1}",
            "ingredients": [
                {"name": f"ingredient {number}", "quantity": "1 cup"}
                for number in range(1, 6)
            ],
            "instructions": "Cook until ready.",
        })
    return {
        "dietary_preference": "omnivore",
        "recipes": recipes,
        "shopping_list": [{"name": "rice", "total_quantity": "2 cups", "used_in": ["Dish 1"]}],
    }


class MealPlannerHelperTests(unittest.TestCase):
    def test_normalize_optional_text(self):
        self.assertEqual(normalize_optional_text("  Italian  ", "random"), "Italian")
        self.assertEqual(normalize_optional_text("   ", "random"), "random")
        self.assertEqual(normalize_optional_text(None, "random"), "random")

    def test_format_ingredients(self):
        self.assertEqual(
            format_ingredients([{"quantity": "2 cups", "name": "rice"}]),
            "2 cups rice",
        )

    def test_extract_json_with_prose_and_fences(self):
        self.assertEqual(extract_json_object('Plan:\n```json\n{"ok": true}\n```'), {"ok": True})
        with self.assertRaises(json.JSONDecodeError):
            extract_json_object('{"outer": {"partial": }}')

    def test_parse_meal_plan_and_schema_validation(self):
        encoded = json.dumps(sample_meal_plan())
        self.assertEqual(len(parse_meal_plan(f"Here is your plan:\n{encoded}")["recipes"]), 7)

        invalid = sample_meal_plan()
        invalid["recipes"].pop()
        with self.assertRaises(ValueError):
            parse_meal_plan(json.dumps(invalid))

    def test_generator_returns_consistent_failure_shape_for_missing_key(self):
        with patch("meal_planner.get_llm", side_effect=ValueError("OPENAI_API_KEY is missing")):
            result = MealPlanGenerator().generate("omnivore")
        self.assertEqual(set(result), {"success", "meal_plan", "generation_time", "error"})
        self.assertFalse(result["success"])
        self.assertIn("MealPlanGenerator.generate", result["error"])
        self.assertIn("OPENAI_API_KEY is missing", result["error"])

    def test_generator_reports_invalid_json_location(self):
        with patch.object(MealPlanGenerator, "_invoke_model", return_value='{"recipes": [}'):
            result = MealPlanGenerator(llm=object()).generate("omnivore")
        self.assertFalse(result["success"])
        self.assertIn("JSON response line 1, column", result["error"])

    def test_email_message_formats_plan_without_sending(self):
        message = build_email_message("from@example.com", "to@example.com", sample_meal_plan())
        body = message.get_payload()[0].get_payload(decode=True).decode()
        self.assertEqual(message["To"], "to@example.com")
        self.assertIn("WEEKLY MEAL PLAN (OMNIVORE)", body)
        self.assertIn("1 cup ingredient 1", body)

    def test_basic_validation_uses_meal_plan_schema(self):
        result = {"success": True, "meal_plan": sample_meal_plan()}
        self.assertEqual(run_basic_validation(result), {"passed": True, "errors": []})

    def test_load_test_cases_reports_missing_and_malformed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.json"
            with self.assertRaisesRegex(FileNotFoundError, "load_test_cases"):
                load_test_cases(path)

            path.write_text("[{broken]", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 1, column"):
                load_test_cases(path)

            path.write_text('[{"dietary_preference": "pescatarian"}]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported dietary_preference"):
                load_test_cases(path)

    def test_load_repository_test_cases(self):
        cases = load_test_cases(Path(__file__).with_name("test_cases.json"))
        self.assertEqual(len(cases), 4)


if __name__ == "__main__":
    unittest.main()
