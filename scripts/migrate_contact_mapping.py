import sys
import os

# Adds the project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.database import engine
from data.models import ContactMapping

print("Criando tabela 'contact_mappings'...")
ContactMapping.__table__.create(bind=engine, checkfirst=True)
print("Tabela criada com sucesso!")
