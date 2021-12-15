import warnings
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from pprint import pprint
from typing import Tuple, Union, Optional, Any, List

from SourceIO.library.shared.content_providers.content_manager import ContentManager


class FGDLexerException(Exception):
    pass


class FGDParserException(Exception):
    pass


class VKVToken(Enum):
    STRING = "String literal"
    NUMERIC = "Numeric literal"
    IDENTIFIER = "Identifier literal"
    COMMENT = "Comment literal"
    LPAREN = "("
    RPAREN = ")"
    LBRACKET = "["
    RBRACKET = "]"
    LBRACE = "{"
    RBRACE = "}"
    NEWLINE = "\n"
    EOF = "End of file"


class ValveKeyValueLexer:

    def __init__(self, buffer: str, buffer_name: str = '<memory>'):
        self.buffer = buffer.replace('\r\n', '\n')
        self.buffer_name = buffer_name
        self._offset = 0
        self._line = 1
        self._column = 1

    @property
    def symbol(self):
        if self._offset < len(self.buffer):
            return self.buffer[self._offset]
        else:
            return ""

    @property
    def next_symbol(self):
        if self._offset + 1 < len(self.buffer):
            return self.buffer[self._offset + 1]
        else:
            return ""

    @property
    def leftover(self):
        return self.buffer[self._offset:]

    @property
    def line(self):
        return self._line

    @property
    def column(self):
        return self._column

    def advance(self):
        symbol = self.symbol
        if symbol:
            if symbol == '\r' and self.next_symbol == '\n':
                self._offset += 1

            if symbol == '\n' or symbol == '\r':
                self._line += 1
                self._column = 1
            else:
                self._column += 1
            self._offset += 1
        return symbol

    def skip(self, count=1):
        for _ in range(count):
            self.advance()

    @staticmethod
    def _is_valid_symbol(symbol):
        return (symbol.isprintable() or symbol in '\t') and symbol not in '{}[]"\'\n\r'

    def _is_valid_quoted_symbol(self, symbol):
        return self._is_valid_symbol(symbol) or symbol in '$%.,\\/<>=![]{}?'

    def _is_escaped_symbol(self):
        return self.next_symbol in '\'"\\'

    def read_simple_string(self, terminators='\n'):
        string_buffer = ""
        while True:
            symbol = self.symbol
            # if symbol in '"\'':
            #     self.advance()
            #     break
            if not self._is_valid_symbol(symbol) or symbol in terminators:
                break
            string_buffer += self.advance()
        return string_buffer.strip().rstrip()

    def read_quoted_string(self):
        terminator = self.advance()
        string_buffer = ""

        while True:
            symbol = self.symbol
            if symbol == '\\' and self.next_symbol in '\'"\n\t\r':
                self.advance()
            if not self._is_valid_quoted_symbol(symbol) or symbol in terminator + '\n':
                break

            string_buffer += self.advance()
        if self.symbol == terminator:
            self.advance()
        else:
            warnings.warn(f'Expected {terminator!r}, but got {self.symbol !r} at {self._line}:{self._column}')

        return string_buffer.strip().rstrip()

    def lex(self):
        while self._offset < len(self.buffer):
            if self.symbol == '\n':
                if self.next_symbol == '\n':
                    while self.next_symbol == '\n':
                        self.advance()  # skip multiple new lines
                yield VKVToken.NEWLINE, self.advance()
            elif self.symbol.isspace():
                self.advance()
                continue
            elif self.symbol == '/' and self.next_symbol == '/':
                self.advance(), self.advance()
                comment = ''
                while self.symbol != '\n' and self:
                    comment += self.advance()
                # yield VKVToken.COMMENT, comment
            elif self.symbol == '{':
                yield VKVToken.LBRACE, self.advance()
            elif self.symbol == '}':
                yield VKVToken.RBRACE, self.advance()
            elif self._is_valid_symbol(self.symbol):
                string = self.read_simple_string(terminators=' \t\n')
                if string:
                    yield VKVToken.STRING, string
            elif self.symbol in '\'"':
                if self.next_symbol in '\'"':
                    self.advance(), self.advance()
                    yield VKVToken.STRING, ""
                    continue
                string = self.read_quoted_string()
                if string:
                    yield VKVToken.STRING, string
            elif self.symbol == '[':
                yield VKVToken.LBRACKET, self.advance()
            elif self.symbol == ']':
                yield VKVToken.RBRACKET, self.advance()
            elif self.symbol.isprintable():
                warnings.warn(f'Unknown symbol {self.advance()!r} at {self._line}:{self._column}')
                continue
            else:
                raise FGDLexerException(
                    f'Unknown symbol {self.symbol!r} in {self.buffer_name!r} at {self._line}:{self._column}')
        yield VKVToken.EOF, None

    def __bool__(self):
        return self._offset < len(self.buffer)


class ValveKeyValueParser:
    def __init__(self, path: Union[Path, str] = None, buffer_and_name: Tuple[str, str] = None, self_recover=False):
        if path is not None:
            self._path = Path(path)
            with self._path.open() as f:
                self._lexer = ValveKeyValueLexer(f.read(), str(self._path))
        elif buffer_and_name is not None:
            self._lexer = ValveKeyValueLexer(*buffer_and_name)
            self._path = buffer_and_name[1]
        self._tokens = self._lexer.lex()
        self._last_peek = None
        self._self_recover = self_recover

        self.tree = {}

    def peek(self):
        if self._last_peek is None:
            self._last_peek = next(self._tokens)
        return self._last_peek

    def advance(self):
        if self._last_peek is not None:
            ret = self._last_peek
            self._last_peek = None
            return ret
        return next(self._tokens)

    def expect(self, token_type):
        token, value = self.peek()
        if token == token_type:
            self.advance()
            return value
        else:
            if self._self_recover:
                warnings.warn(f"Trying to recover from unexpected token {token}:{value!r}, expected {token_type} "
                              f"in {self._path!r} at {self._lexer.line}:{self._lexer.column}")
                while not self.match(VKVToken.NEWLINE):
                    self.advance()
                pass
            else:
                raise FGDParserException(f"Unexpected token {token}:{value!r}, expected {token_type}"
                                         f"in {self._path!r} at {self._lexer.line}:{self._lexer.column}")

    def match(self, token_type, consume=False):
        token, value = self.peek()
        if token == token_type:
            if consume:
                self.advance()
            return True
        return False

    def _skip_newlines(self):
        while self.match(VKVToken.NEWLINE):
            self.advance()

    def parse(self):
        node_stack = [self.tree]
        while self._lexer:
            self._skip_newlines()
            if self.match(VKVToken.STRING):
                key = self.advance()[1]
                self._skip_newlines()
                if self.match(VKVToken.LBRACE, True):
                    new_tree_node = {}
                    node_stack[-1][key] = new_tree_node
                    node_stack.append(new_tree_node)
                elif self.match(VKVToken.STRING):
                    value = self.advance()
                    if self.match(VKVToken.LBRACKET, True):
                        condition = self.advance()
                        self.expect(VKVToken.RBRACKET)
                        node_stack[-1][key] = value[1], condition[1]
                    else:
                        node_stack[-1][key] = value[1]
                    self.expect(VKVToken.NEWLINE)
            elif self.match(VKVToken.RBRACE, True):
                node_stack.pop(-1)
            elif self.match(VKVToken.EOF):
                break
            else:
                token, value = self.peek()
                raise FGDParserException(
                    f"Unexpected token {token}:\"{value}\" in {self._path} at {self._lexer.line}:{self._lexer.column}")


if __name__ == '__main__':
    data = """Shader
{
    $key"  "value
    "$key1"  "value1'
    "$key2"  "valu\\"e1"
    "$key3"  "val\\ue2`
    "$key4"  "value3"  [!PS3]
    "$key5"  "[0.0 1.0 1.0]"
    "$key6"  models/tests/basic_vertexlitgeneric
    
    test "<>!=asd./"
    
    $envtint  .3 .3 .3
    
    $basetexturetransform  "center .5 .5 scale 1 1 rotate 16 translate 15 8"
    ">=DX80"{
        key1 valie1
    
    }
    Proxies{
        Test
        {
           key1 valie1
        }
        test   test
    
    }
}
"""

    debug_data = """replacements
{
	templates
	{
		standard
		{
			"VertexLitGeneric"
			{
				pyro_vision
				{
					$EFFECT					1
					$VERTEX_LIT				1
		
					$basetexture			$basetexture
					$vertexcolor			$vertexcolor  [PS3]

//					$STRIPETEXTURE			"rj/stripe3"
//					$STRIPE_SCALE			"[ 0.002 0.002 0.002 ]"
//					$STRIPE_COLOR			"[ 1.0 0.0 0.7 ]"
		
					$COLORBAR				"rj/colorbar_peach02"

					$DIFFUSE_WHITE			0.5
					$GRAY_POWER				0.45
					$GRAY_STEP				"[ -0.1 0.85 ]"
					$LIGHTMAP_GRADIENTS		255
				}
			}
		}

	}

	patterns
	{	barrel_crate_doomsday
		{
			template	"standard"
		}
		rocket_
		{
			template	"standard"
		}
		wood_fence
		{
			template	"standard"
		}
		western_wood
		{
			template	"standard"
		}
	}
}"""

    print(debug_data)
    parser = ValveKeyValueParser(buffer_and_name=(debug_data, 'memory'), self_recover=True)
    parser.parse()
    pprint(parser.tree)
    # ContentManager().scan_for_content(r"H:\SteamLibrary\SteamApps\common\SourceFilmmaker\game\Furry")
    # for file_name, file in ContentManager().glob('*.vmt'):
    #     print(file_name)
    #     file_data = file.read().decode('latin1')
    #     print(file_data)
    #     parser = ValveKeyValueParser(buffer_and_name=(file_data, file_name),self_recover=True)
    #     parser.parse()
    #     pprint(parser.tree)