# Client Management Bot

## Overview

A professional-grade Telegram bot for client management with integrated WhatsApp messaging capabilities. The system provides comprehensive client relationship management including automated messaging, subscription billing, template management, and scheduled reminders. Built with enterprise-level architecture patterns, the bot serves as a centralized platform for managing client communications and billing operations.

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Backend Architecture
- **Framework**: Python-based Telegram bot using python-telegram-bot library
- **Database**: PostgreSQL with SQLAlchemy ORM for data persistence
- **Architecture Pattern**: Modular service-oriented design with clean separation of concerns
- **Session Management**: Conversation-based state management for multi-step user interactions

### Core Services
- **Database Service**: Centralized data access layer managing users, clients, subscriptions, and message templates
- **Scheduler Service**: Automated task scheduling for reminders and recurring operations
- **WhatsApp Service**: Integration with Baileys WhatsApp API for automated messaging
- **Payment Service**: Mercado Pago integration for subscription billing and PIX payments

### Data Model
- **User Management**: Complete user lifecycle with trial periods and subscription tracking
- **Client Management**: Full CRUD operations with contact details, service plans, and billing information
- **Subscription System**: 7-day trial period followed by R$20/month recurring billing
- **Message Templates**: Customizable messaging templates with dynamic variable substitution
- **Audit Logging**: Message delivery tracking and system activity logs

### WhatsApp Integration
- **Technology**: Baileys library for WhatsApp Web API connectivity
- **Multi-Session Support**: Individual WhatsApp sessions per user with persistent authentication
- **Session Management**: Automated QR code generation and session state persistence
- **Message Queue**: Controlled message delivery with rate limiting and retry mechanisms

### Enterprise Features
- **Structured Logging**: JSON-formatted logs with correlation IDs and contextual information
- **Circuit Breaker Pattern**: Resilient external service calls with automatic failure detection
- **Rate Limiting**: Multiple strategies including token bucket and sliding window algorithms
- **Caching System**: LRU cache with TTL support for performance optimization
- **Input Validation**: Comprehensive data sanitization and security checks
- **Health Monitoring**: Real-time system metrics and health check endpoints

### Deployment Architecture
- **Platform**: Railway cloud platform with Nixpacks build system
- **Scalability**: Horizontal scaling support with configurable replica counts
- **Reliability**: Automatic restart policies and failure recovery mechanisms
- **Environment Management**: Centralized configuration with environment variable validation
- **Railway Integration**: Complete deployment configuration with start_railway.py manager
- **Database**: PostgreSQL integration with automatic DATABASE_URL configuration
- **Health Monitoring**: Built-in health check endpoint for service monitoring

## External Dependencies

### Payment Processing
- **Mercado Pago API**: Primary payment gateway for PIX transactions and subscription billing
- **Authentication**: Access token and public key based authentication

### Messaging Services
- **Telegram Bot API**: Core bot functionality and user interface
- **Baileys WhatsApp API**: WhatsApp messaging integration with session management
- **QR Code Generation**: Dynamic QR code creation for WhatsApp authentication

### Database and Storage
- **PostgreSQL**: Primary database for all persistent data storage
- **SQLAlchemy**: Object-relational mapping and database abstraction layer

### Infrastructure Services
- **Railway Platform**: Cloud hosting and deployment automation
- **APScheduler**: Advanced Python scheduler for automated tasks and reminders
- **Gunicorn**: WSGI HTTP server for production deployment

### Development and Monitoring
- **Python Logging**: Structured logging with file and console output
- **Psutil**: System resource monitoring and performance metrics
- **Cryptography**: Security utilities for data encryption and validation