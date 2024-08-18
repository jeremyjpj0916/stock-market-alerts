provider "aws" {
  region = "us-east-1"
}

resource "aws_ecs_cluster" "stock_market_alerts" {
  name = "stock-market-alerts"
}

resource "aws_ecs_task_definition" "stock_market_alerts" {
  family                = "stock-market-alerts"
  requires_compatibilities = ["FARGATE"]
  network_mode           = "awsvpc"
  cpu                    = 256
  memory                 = 512
  execution_role_arn     = aws_iam_role.ecs_task_execution_role.arn

  container_definitions = jsonencode([
    {
      name      = "stock-market-alerts"
      image     = "${aws_account_id}.dkr.ecr.us-east-1.amazonaws.com/stock-market-alerts:latest"
      cpu       = 256
      memory    = 512
      essential = true
      command   = ["python", "-u", "main.py"]
      environment = [
        {
          name  = "stock_symbols"
          value = jsonencode(var.stock_symbols)
        },
        {
          name  = "percent_change_threshold"
          value = jsonencode(var.percent_change_threshold)
        }
      ]
    }
  ])
}

resource "aws_iam_role" "ecs_task_execution_role" {
  name        = "ecs-task-execution-role"
  description = "Execution role for ECS tasks"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Effect = "Allow"
      }
    ]
  })
}

variable "stock_symbols" {
  type        = list(string)
  description = "List of stock symbols"
}

variable "percent_change_threshold" {
  type        = number
  description = "Percent change threshold"
}

variable "aws_account_id" {
  type        = string
  description = "AWS account ID"
}
