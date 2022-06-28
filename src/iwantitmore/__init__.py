import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

#Q: why does pytest/pycharm not show INFO level logs while running tests?
#A: it's their default behaviour, start pytest with --log-level=INFO to see them and also it will only show in case of
#failed test. See https://docs.pytest.org/en/6.2.x/logging.html
#See file pyproject.toml for pytest config