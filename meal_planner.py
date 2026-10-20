"""Core logic for the weekly meal planner notebook."""

import json
import os
import re
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator


MODEL_NAME = "gpt-4o-mini"
TEMPERATURE = 0.7
RECIPES_PER_PLAN = 7
INGREDIENTS_PER_RECIPE = 5


class DietaryPreference(str, Enum):
    OMNIVORE = "omnivore"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    KETO = "keto"


class Ingredient(BaseModel):
    """An ingredient with a quantity."""

    name: str = Field(..., description="Name of the ingredient")
    quantity: str = Field(..., description="Quantity with unit, such as '2 cups'")


class Recipe(BaseModel):
    """A single themed dinner recipe."""

    day: str = Field(..., description="Day of the week")
    theme: str = Field(..., description="Cuisine theme, such as Italian or Mexican")
    dish_name: str = Field(..., description="Name of the dish")
    ingredients: list[Ingredient] = Field(..., description="List of 5 ingredients")
    instructions: str = Field(..., description="Brief cooking instructions")

    @field_validator("ingredients")
    @classmethod
    def validate_ingredient_count(cls, ingredients: list[Ingredient]) -> list[Ingredient]:
        if len(ingredients) != INGREDIENTS_PER_RECIPE:
            raise ValueError(
                f"Recipe must have exactly {INGREDIENTS_PER_RECIPE} ingredients, got {len(ingredients)}"
            )
        return ingredients


class ShoppingItem(BaseModel):
    """A consolidated shopping-list item."""

    name: str = Field(..., description="Ingredient name")
    total_quantity: str = Field(..., description="Total quantity needed")
    used_in: list[str] = Field(..., description="Dishes using this ingredient")


class MealPlan(BaseModel):
    """A complete weekly meal plan."""

    dietary_preference: str = Field(..., description="Selected dietary preference")
    recipes: list[Recipe] = Field(..., description="List of 7 recipes for the week")
    shopping_list: list[ShoppingItem] = Field(..., description="Consolidated shopping list")

    @field_validator("recipes")
    @classmethod
    def validate_recipe_count(cls, recipes: list[Recipe]) -> list[Recipe]:
        if len(recipes) != RECIPES_PER_PLAN:
            raise ValueError(f"Meal plan must have exactly {RECIPES_PER_PLAN} recipes, got {len(recipes)}")
        return recipes

    @field_validator("recipes")
    @classmethod
    def validate_unique_themes(cls, recipes: list[Recipe]) -> list[Recipe]:
        themes = [recipe.theme.strip().casefold() for recipe in recipes]
        if len(themes) != len(set(themes)):
            raise ValueError("All 7 themes must be unique")
        return recipes


MEAL_PLAN_SYSTEM_PROMPT = """You are a professional meal planner. Generate a weekly meal plan with EXACTLY 7 themed dinners.

STRICT RULES - FOLLOW EXACTLY:
1. Each recipe MUST have EXACTLY 5 main ingredients (do NOT count salt, pepper, oil, garlic, or water)
2. ALL 7 THEMES MUST BE COMPLETELY DIFFERENT - no duplicates allowed! Use: Italian, Mexican, Japanese, Indian, Thai, Greek, American, Chinese, French, Korean, Mediterranean, Middle Eastern, etc.
3. All ingredients MUST be real, commonly available items
4. Follow the dietary preference strictly
5. Provide realistic quantities for 2 servings

Dietary Guidelines:
- Omnivore: Any ingredients allowed
- Vegetarian: No meat or fish, eggs and dairy OK
- Vegan: No animal products at all
- Keto: Low carb, high fat, no grains/sugar/starchy vegetables

Output valid JSON matching this exact structure:
{format_instructions}"""
MEAL_PLAN_USER_PROMPT = """Create a weekly meal plan:
- Diet: {dietary_preference}
- Theme requests: {theme_requests}
- Preferences: {additional_preferences}

IMPORTANT: Each day MUST have a DIFFERENT cuisine theme. Generate 7 unique themed dinners for Monday-Sunday."""


def normalize_optional_text(value: str | None, default: str) -> str:
    """Return trimmed input text or its default value."""
    return value.strip() if isinstance(value, str) and value.strip() else default


def format_ingredients(ingredients: list[dict[str, Any]]) -> str:
    """Format ingredient dictionaries as a comma-separated line."""
    return ", ".join(f"{item['quantity']} {item['name']}" for item in ingredients)


def format_error(function_name: str, error: Exception, location: str, suggestion: str) -> str:
    """Build a consistent, actionable error message."""
    return (
        f"{function_name} failed at {location} ({type(error).__name__}): {error}. "
        f"Suggestion: {suggestion}"
    )


def extract_json_object(content: str) -> dict[str, Any]:
    """Find and decode the first JSON object, including prose or fences around it."""
    cleaned = re.sub(r"```\s*(?:json)?\s*", "", content, flags=re.IGNORECASE)
    cleaned = cleaned.replace("```", "")
    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    if start == -1:
        raise ValueError("No JSON object was found in the model response")
    value, _ = decoder.raw_decode(cleaned[start:])
    if not isinstance(value, dict):
        raise ValueError("The model response JSON must be an object")
    return value


def parse_meal_plan(content: str) -> dict[str, Any]:
    """Decode and validate a model response as a meal plan."""
    return MealPlan.model_validate(extract_json_object(content)).model_dump()


def get_llm() -> Any:
    """Create the configured OpenAI chat model after checking credentials."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing")
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as error:
        raise RuntimeError("langchain-openai is not installed") from error
    return ChatOpenAI(model=MODEL_NAME, temperature=TEMPERATURE, api_key=api_key)


def _validation_messages(error: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(part) for part in item['loc']) or 'meal_plan'}: {item['msg']}"
        for item in error.errors()
    ]


def _openai_error_details(error: Exception) -> tuple[str, str] | None:
    """Match provider errors to the part of the request the user can fix."""
    try:
        from openai import (
            APIConnectionError,
            APIError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )
    except ImportError:
        return None

    cases = (
        (AuthenticationError, "OpenAI authentication", "Check that OPENAI_API_KEY is valid and active."),
        (RateLimitError, "OpenAI rate limit", "Wait briefly, then retry or check the account's usage limits."),
        (APITimeoutError, "OpenAI request timeout", "Retry when the service or network is responsive."),
        (APIConnectionError, "OpenAI network connection", "Check the internet connection and retry."),
        (APIError, "OpenAI service request", "Check the request and OpenAI service status, then retry."),
    )
    for error_type, location, suggestion in cases:
        if isinstance(error, error_type):
            return location, suggestion
    return None


class MealPlanGenerator:
    """Coordinate the model request, response parsing, and meal-plan validation."""

    def __init__(self, llm: Any = None):
        self.llm = llm

    def _invoke_model(self, request: dict[str, str]) -> str:
        """Call the model and return its text response."""
        llm = self.llm if self.llm is not None else get_llm()
        try:
            from langchain_core.prompts import ChatPromptTemplate
        except ImportError as error:
            raise RuntimeError("langchain is not installed") from error
        prompt = ChatPromptTemplate.from_messages([
            ("system", MEAL_PLAN_SYSTEM_PROMPT),
            ("human", MEAL_PLAN_USER_PROMPT),
        ])
        response = (prompt | llm).invoke(request)
        return response.content

    def generate(
        self,
        dietary_preference: str,
        theme_requests: str | None = "random",
        additional_preferences: str | None = "none",
    ) -> dict[str, Any]:
        """Generate a validated meal plan and return a consistent result shape."""
        started_at = time.monotonic()
        try:
            request = {
                "dietary_preference": dietary_preference,
                "theme_requests": normalize_optional_text(theme_requests, "random diverse cuisines"),
                "additional_preferences": normalize_optional_text(additional_preferences, "none"),
                "format_instructions": json.dumps(MealPlan.model_json_schema(), indent=2),
            }
            content = self._invoke_model(request)
            meal_plan = parse_meal_plan(content)
            return {
                "success": True,
                "meal_plan": meal_plan,
                "generation_time": round(time.monotonic() - started_at, 2),
                "error": None,
            }
        except json.JSONDecodeError as error:
            message = format_error(
                "MealPlanGenerator.generate",
                error,
                f"JSON response line {error.lineno}, column {error.colno}",
                "Retry generation or inspect the model response format.",
            )
        except ValidationError as error:
            message = format_error(
                "MealPlanGenerator.generate",
                error,
                "MealPlan schema validation",
                "; ".join(_validation_messages(error)),
            )
        except ValueError as error:
            message = format_error(
                "MealPlanGenerator.generate",
                error,
                "configuration or model response parsing",
                "Set OPENAI_API_KEY in .env and check that the model returned a JSON object.",
            )
        except Exception as error:
            api_error_details = _openai_error_details(error)
            if api_error_details:
                location, suggestion = api_error_details
                message = format_error("MealPlanGenerator.generate", error, location, suggestion)
                return {
                    "success": False,
                    "meal_plan": None,
                    "generation_time": round(time.monotonic() - started_at, 2),
                    "error": message,
                }
            message = format_error(
                "MealPlanGenerator.generate",
                error,
                "model request or response processing",
                "Check the request, installed packages, and service status.",
            )

        return {
            "success": False,
            "meal_plan": None,
            "generation_time": round(time.monotonic() - started_at, 2),
            "error": message,
        }


def run_basic_validation(meal_plan_result: dict[str, Any]) -> dict[str, Any]:
    """Validate the result through the same schema used by generation."""
    if not meal_plan_result.get("success"):
        return {"passed": False, "errors": [meal_plan_result.get("error") or "Generation failed"]}
    try:
        MealPlan.model_validate(meal_plan_result["meal_plan"])
    except (ValidationError, KeyError, TypeError) as error:
        if isinstance(error, ValidationError):
            errors = _validation_messages(error)
        else:
            errors = [str(error)]
        return {"passed": False, "errors": errors}
    return {"passed": True, "errors": []}


def format_meal_plan_email(meal_plan: dict[str, Any]) -> str:
    """Format the shopping list and recipes as plain-text email content."""
    lines = [
        "=" * 40,
        f"WEEKLY MEAL PLAN ({meal_plan['dietary_preference'].upper()})",
        "=" * 40,
        "",
        "SHOPPING LIST",
        "-" * 20,
    ]
    lines.extend(f"- {item['total_quantity']} {item['name']}" for item in meal_plan["shopping_list"])
    lines.extend(["", "=" * 40, "RECIPES", "=" * 40])

    for recipe in meal_plan["recipes"]:
        lines.extend([
            f"\n{recipe['day'].upper()} - {recipe['theme']}",
            recipe["dish_name"],
            f"Ingredients: {format_ingredients(recipe['ingredients'])}",
            f"Instructions: {recipe['instructions']}",
        ])
    return "\n".join(lines)


def build_email_message(sender: str, recipient: str, meal_plan: dict[str, Any]) -> MIMEMultipart:
    """Build the email message without connecting to an email server."""
    message = MIMEMultipart()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Your Weekly Meal Plan"
    message.attach(MIMEText(format_meal_plan_email(meal_plan), "plain"))
    return message


def send_meal_plan_email(recipient_email: str, meal_plan: dict[str, Any]) -> dict[str, Any]:
    """Send a meal-plan email using Gmail SMTP and environment credentials."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    if not sender or not password:
        error = ValueError("EMAIL_SENDER or EMAIL_PASSWORD is missing")
        return {
            "success": False,
            "message": format_error(
                "send_meal_plan_email",
                error,
                "email configuration",
                "Set EMAIL_SENDER and EMAIL_PASSWORD in .env using a Gmail app password.",
            ),
        }

    try:
        message = build_email_message(sender, recipient_email, meal_plan)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(sender, password)
            server.send_message(message)
        return {"success": True, "message": f"Email sent to {recipient_email}"}
    except (smtplib.SMTPException, OSError, KeyError, TypeError) as error:
        return {
            "success": False,
            "message": format_error(
                "send_meal_plan_email",
                error,
                "Gmail SMTP connection or message formatting",
                "Check the recipient, internet connection, Gmail address, and app password.",
            ),
        }


def load_test_cases(path: str | Path) -> list[dict[str, str]]:
    """Load and validate generation test cases from a JSON file."""
    test_path = Path(path)
    try:
        with test_path.open(encoding="utf-8") as file:
            cases = json.load(file)
    except FileNotFoundError as error:
        raise FileNotFoundError(
            format_error("load_test_cases", error, str(test_path), "Check the path or restore the JSON file.")
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            format_error(
                "load_test_cases",
                error,
                f"{test_path}, line {error.lineno}, column {error.colno}",
                "Fix the JSON syntax and try loading the file again.",
            )
        ) from error
    except OSError as error:
        raise OSError(
            format_error("load_test_cases", error, str(test_path), "Check file permissions and the file path.")
        ) from error

    if not isinstance(cases, list):
        raise ValueError(f"load_test_cases: {test_path} must contain a JSON array")

    required = {"dietary_preference"}
    allowed = required | {"theme_requests", "additional_preferences"}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise ValueError(f"load_test_cases: item {index} in {test_path} must be an object")
        missing = required - case.keys()
        unknown = case.keys() - allowed
        if missing or unknown:
            raise ValueError(
                f"load_test_cases: item {index} in {test_path} has missing fields {sorted(missing)} "
                f"and unknown fields {sorted(unknown)}"
            )
        diet = case["dietary_preference"]
        if not isinstance(diet, str) or diet not in {preference.value for preference in DietaryPreference}:
            raise ValueError(
                f"load_test_cases: item {index} has unsupported dietary_preference "
                f"{diet!r}"
            )
    return cases
