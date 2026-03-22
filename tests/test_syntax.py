#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты синтаксического анализа Python кода проекта.

Проверяет:
- Корректность синтаксиса всех .py файлов
- Валидность импортов
- Структуру AST
"""

import ast
import os
from pathlib import Path
from typing import List

import pytest


# === КОНСТАНТЫ ===

EXCLUDE_DIRS = {
    '__pycache__',
    '.git',
    '.pytest_cache',
    'venv',
    'env',
    '.venv',
    'node_modules',
    '.tox',
    'build',
    'dist',
    '.eggs'
}

PROJECT_ROOT = Path(__file__).parent.parent


# === ФИКСТУРЫ ===

@pytest.fixture(scope="session")
def project_root() -> Path:
    """Корневая директория проекта."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def python_files(project_root: Path) -> List[Path]:
    """Список всех Python файлов в проекте."""
    files = []
    
    for root, dirs, filenames in os.walk(project_root):
        # Фильтруем исключенные директории
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for filename in filenames:
            if filename.endswith('.py'):
                files.append(Path(root) / filename)
    
    return files


# === ТЕСТЫ ===

@pytest.mark.syntax
class TestPythonSyntax:
    """Тесты синтаксиса Python файлов."""

    def test_files_found(self, python_files: List[Path]):
        """Проверяет, что найдены Python файлы."""
        assert len(python_files) > 0, "Не найдено ни одного Python файла"
        print(f"✓ Найдено {len(python_files)} Python файлов")
    
    def test_all_files_syntax(self, python_files: List[Path]):
        """Проверяет синтаксис всех Python файлов."""
        errors = []
        
        for file_path in python_files:
            error = self._check_file_syntax(file_path)
            if error:
                errors.append(error)
        
        if errors:
            error_report = "\n\n".join(errors)
            pytest.fail(
                f"Найдены синтаксические ошибки в {len(errors)} файлах:\n\n{error_report}"
            )
        
        print(f"✓ Проверено {len(python_files)} файлов - ошибок не найдено")
    
    @staticmethod
    def _check_file_syntax(file_path: Path) -> str | None:
        """Проверяет синтаксис одного файла.
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            Сообщение об ошибке или None
        """
        encodings = ['utf-8', 'cp1251', 'latin-1']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    source_code = f.read()
                
                ast.parse(source_code, filename=str(file_path))
                return None  # Успешно
                
            except UnicodeDecodeError:
                continue  # Пробуем следующую кодировку
                
            except SyntaxError as e:
                return (
                    f"Синтаксическая ошибка в {file_path.relative_to(PROJECT_ROOT)}:\n"
                    f"  Строка {e.lineno}: {e.text}\n"
                    f"  {e.msg}"
                )
            except Exception as e:
                return f"Неожиданная ошибка в {file_path.relative_to(PROJECT_ROOT)}: {e}"
        
        return f"Не удалось прочитать {file_path.relative_to(PROJECT_ROOT)} ни в одной кодировке"

    def test_imports_structure(self, python_files: List[Path]):
        """Проверяет корректность структуры импортов."""
        errors = []
        
        for file_path in python_files:
            file_errors = self._check_imports(file_path)
            errors.extend(file_errors)
        
        if errors:
            error_report = "\n".join(errors)
            pytest.fail(f"Найдены ошибки в импортах:\n{error_report}")
        
        print(f"✓ Проверены импорты в {len(python_files)} файлах - ошибок не найдено")
    
    @staticmethod
    def _check_imports(file_path: Path) -> List[str]:
        """Проверяет импорты в файле.
        
        Args:
            file_path: Путь к файлу
            
        Returns:
            Список сообщений об ошибках
        """
        errors = []
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                source_code = f.read()
            
            tree = ast.parse(source_code, filename=str(file_path))
            relative_path = file_path.relative_to(PROJECT_ROOT)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # Проверка некорректных относительных импортов
                    if node.module is None and node.level == 0:
                        errors.append(
                            f"Некорректный импорт в {relative_path}, строка {node.lineno}"
                        )
                    
                    # Проверка звездочных импортов (code smell)
                    for alias in node.names:
                        if alias.name == '*':
                            errors.append(
                                f"Звездочный импорт (import *) в {relative_path}, "
                                f"строка {node.lineno}. Используйте явные импорты."
                            )
        
        except SyntaxError:
            # Синтаксические ошибки обрабатываются в другом тесте
            pass
        except Exception as e:
            errors.append(f"Ошибка при проверке импортов в {file_path.name}: {e}")
        
        return errors


@pytest.mark.syntax
class TestCodeQuality:
    """Тесты качества кода."""
    
    def test_no_print_statements(self, python_files: List[Path]):
        """Проверяет отсутствие print() в production коде."""
        violations = []
        
        # Исключаем тестовые и служебные файлы
        exclude_files = {'test_', 'run_', 'setup', '__init__.py'}
        
        for file_path in python_files:
            if any(pattern in file_path.name for pattern in exclude_files):
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    source_code = f.read()
                
                tree = ast.parse(source_code, filename=str(file_path))
                
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and node.func.id == 'print':
                            violations.append(
                                f"{file_path.relative_to(PROJECT_ROOT)}, "
                                f"строка {node.lineno}: использование print()"
                            )
            
            except (SyntaxError, UnicodeDecodeError):
                pass
        
        if violations:
            pytest.fail(
                "Найдены print() в production коде:\n" + "\n".join(violations) +
                "\n\nИспользуйте logger вместо print()"
            )
        
        print("✓ print() не найдены в production коде")
