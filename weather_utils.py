# weather_utils.py

"""
This module contains utilities for weather and climate data processing.
"""

import requests

class WeatherUtils:
    @staticmethod
    def get_current_weather(city: str, api_key: str) -> dict:
        """
        Fetches the current weather for the given city.
         
        :param city: Name of the city
        :param api_key: API key for the weather service
        :return: Weather data in dictionary format
        """
        url = f"http://api.openweathermap.org/data/2.5/weather?q={{city}}&appid={{api_key}}"
        response = requests.get(url)
        return response.json()

    @staticmethod
    def convert_kelvin_to_celsius(kelvin: float) -> float:
        """
        Converts temperature from Kelvin to Celsius.
         
        :param kelvin: Temperature in Kelvin
        :return: Temperature in Celsius
        """
        return kelvin - 273.15

    @staticmethod
    def format_weather_data(weather_data: dict) -> str:
        """
        Formats the raw weather data into a human-readable string.
         
        :param weather_data: Raw weather data
        :return: Formatted weather information
        """
        city = weather_data['name']
        temperature = WeatherUtils.convert_kelvin_to_celsius(weather_data['main']['temp'])
        description = weather_data['weather'][0]['description']
        return f"Current weather in {{city}}: {{temperature:.2f}}°C, {{description}}"
