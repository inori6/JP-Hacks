from functools import wraps
# 最简单的无参数装饰器
def deco(func):
    @wraps(func)
    def print_add():
        print(f"{func.__name__}")
        func()
    return print_add

@deco
def hello():
    print("hello")

#带参数型装饰器
def test_function(text:str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args,**kwargs):
            print("这是传入的字符串：",text)
            func(*args,**kwargs) # 单星号参数适配任意数量位置的参数，双星号参数适配任意数量关键字参数，注意：位置参数适配必须在关键字之前
        return wrapper
    return decorator

@test_function("my name is Ryuunosuke")
def deco_check():
    print("this is a check")

if __name__ == "__main__":
    deco_check()
    print(deco_check.__name__)