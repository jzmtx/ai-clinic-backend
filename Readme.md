# REAL TIME TOKEN MANAGMENT SYSTEM

## 🌟 Project Overview

This is a full-stack web application designed for clinic administration, patient management, and communication. The system automates appointment booking, manages doctor and patient data, and features a real-time patient token/queue system with SMS notifications.And rural people or nontech people can book token using ivr call 

---

## 💻 Technology Stack

| Component | Technology | Version Note |
| :--- | :--- | :--- |
| **Backend** | Python (Django & DRF) | **Must be Python 3.12 or less** (to avoid dependency conflicts) |
| **Database** | SQLite3 (Default) | Built-in |
| **External API** | Twilio | For SMS notifications and reminders |
| **Frontend** | Node.js (React/JavaScript) | Requires stable Node.js/npm |
| **Tooling** | Git, ngrok | For source control and public server access |

---

## 🚀 Getting Started

### Prerequisites

You must have the following software installed and configured:

1.  **Git** (Added to System PATH)
2.  **Python 3.12** (Recommended stable version)
3.  **Node.js & npm**
4.  **Microsoft Visual C++ Build Tools** (For Python packages that require compilation)
5.  **Ngrok** (Downloaded and authenticated with `ngrok config add-authtoken <token>`)

### 1. Cloning the Repository

Create a main directory for the project and clone both repositories inside it.

```bash
# Create and enter main project folder
mkdir ai-clinic-fullstack
cd ai-clinic-fullstack

# Clone Backend (Python/Django)
git clone [https://github.com/jzmtx/ai-clinic-backend](https://github.com/jzmtx/ai-clinic-backend)

# Clone Frontend (React/Node.js)
git clone [https://github.com/jzmtx/ai-clinic-frontend](https://github.com/jzmtx/ai-clinic-frontend)