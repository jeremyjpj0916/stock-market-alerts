# Use an official Python image as the base
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Copy the repo code
COPY . .

# Install the dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the command to start the script
CMD ["python", "main.py"]
