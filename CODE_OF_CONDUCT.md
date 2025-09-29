# 代码规范 (Code of Conduct)

## Import 导入规范

### 1. 多项导入排序规则

在同一个 `from...import` 语句中导入多个对象时，应按照以下顺序排列：

- 类 (Classes)
- 变量 (Variables)  
- 函数 (Functions)

**示例：**

```python
from ncatbot.plugin_system import (
    NcatBotPlugin,          # 类
    command_registry,       # 变量
    group_filter,           # 函数
    option,                 # 函数
    param,                  # 函数
    root_filter,            # 函数
)
```

### 2. 同级导入字母排序

同级别的导入语句应按照模块名的字母顺序排列。

**示例：**

```python
from ncatbot.core.event import GroupMessageEvent
from ncatbot.plugin_system import (
    NcatBotPlugin,
    command_registry,
    group_filter,
    option,
    param,
    root_filter,
)
from ncatbot.utils import config, get_log
```

### 3. 导入分组结构

所有导入语句应分为三个独立的组，每组之间用空行分隔：

1. **ncatbot 框架模块** - 项目核心框架相关的导入
2. **项目自定义模块** - 当前项目内部模块的导入
3. **第三方依赖** - 外部库和 Python 标准库的导入

**示例：**

```python
# ncatbot 框架模块
from ncatbot.core.event import GroupMessageEvent
from ncatbot.plugin_system import NcatBotPlugin, command_registry, group_filter
from ncatbot.utils import config, get_log

# 项目自定义模块
from .webhook import GitHubWebhookHandler

# 第三方依赖
import threading
import yaml
from typing import Any, Dict, Optional
```

### 4. 导入语句类型排序

在同一分组内，应优先使用 `import` 语句，再使用 `from...import` 语句。

**示例：**

```python
import threading
import yaml
from typing import Any, Dict, Optional
```

### 5. 模块层级深度排序

当导入具有父子关系的模块时，应按照模块路径的深度进行排序，浅层模块在前，深层模块在后。

**示例：**

```python
from ncatbot.core import GroupMessage
from ncatbot.core.event import GroupMessageEvent
```

---

**注意事项：**

- 所有导入语句应放在文件顶部，仅次于文件注释和模块文档字符串
- 避免使用通配符导入 (`from module import *`)
- 对于较长的导入列表，建议使用括号进行多行格式化，便于阅读和维护
